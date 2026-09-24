import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../models/models.dart';
import 'api_client.dart';
import 'config.dart';
import 'image_cache.dart';

enum AuthStage {
  /// Reading the stored token and asking the server who we are.
  starting,

  /// No usable token — show sign-in.
  signedOut,

  /// Signed in but still behind the ID-card wall (tier 0, or tier 1 read-only).
  needsVerification,

  /// Verified. The whole app is open.
  ready,
}

/// The single source of truth for "who is using this app right now".
///
/// Screens never store a token; they read [stage] and [me] from here, and the
/// client is handed the token automatically whenever it changes.
class Session extends ChangeNotifier {
  Session(this.api) {
    api.onUnauthenticated = _onTokenRejected;
  }

  final ApiClient api;
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );
  static const _tokenKey = 'session_token';

  AuthStage _stage = AuthStage.starting;
  Me? _me;
  String? _startupError;
  bool _busy = false;

  AuthStage get stage => _stage;
  Me? get me => _me;
  String? get startupError => _startupError;
  bool get busy => _busy;
  bool get canWrite => _me?.canWrite ?? false;
  int get unread => _me?.unread ?? 0;

  Future<void> bootstrap() async {
    _stage = AuthStage.starting;
    _startupError = null;
    notifyListeners();

    await AppConfig.load();
    String? token;
    try {
      token = await _storage.read(key: _tokenKey);
    } catch (_) {
      // A corrupt keystore entry (app reinstalled over itself, for instance)
      // must not brick the app — fall through to signed out.
      token = null;
    }

    if (token == null || token.isEmpty) {
      _stage = AuthStage.signedOut;
      notifyListeners();
      return;
    }

    api.token = token;
    try {
      await refreshMe();
    } on ApiException catch (error) {
      if (error.needsLogin) {
        await _clearToken();
        _stage = AuthStage.signedOut;
      } else {
        // The server is unreachable rather than rejecting us. Keep the token and
        // let the user retry instead of silently signing them out.
        _startupError = error.message;
        _stage = AuthStage.signedOut;
      }
      notifyListeners();
    }
  }

  Future<void> refreshMe() async {
    final data = await api.get('/auth/me') as Map<String, dynamic>;
    _me = Me.fromJson(data);
    _stage = _me!.canWrite ? AuthStage.ready : AuthStage.needsVerification;
    notifyListeners();
  }

  /// Cheap poll used by the shell to keep the notifications badge honest.
  Future<void> refreshUnread() async {
    if (_me == null || !_me!.canRead) return;
    try {
      final data =
          await api.get('/notifications/unread-count') as Map<String, dynamic>;
      final unread = (data['unread'] as num?)?.toInt() ?? 0;
      if (unread != _me!.unread) {
        await refreshMe();
      }
    } on ApiException {
      // A badge is not worth an error toast.
    }
  }

  Future<void> login(String identifier, String password) => _authenticate(
      '/auth/login', {'identifier': identifier, 'password': password});

  Future<void> signup(String handle, String fullName, String password) =>
      _authenticate(
        '/auth/signup',
        {
          'handle': handle,
          'full_name': fullName,
          'password': password,
          'consent': true
        },
      );

  Future<void> _authenticate(String path, Map<String, dynamic> body) async {
    _busy = true;
    notifyListeners();
    try {
      final data = await api.post(path, body: body) as Map<String, dynamic>;
      final token = data['token'] as String;
      api.token = token;
      await _storage.write(key: _tokenKey, value: token);
      _me = Me.fromJson((data['user'] as Map).cast<String, dynamic>());
      _stage = _me!.canWrite ? AuthStage.ready : AuthStage.needsVerification;
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  Future<void> logout() async {
    try {
      await api.post('/auth/logout');
    } on ApiException {
      // Revoking server-side is best effort; the local token goes either way.
    }
    await _clearToken();
    await CampusImageCache.clear();
    _me = null;
    _stage = AuthStage.signedOut;
    notifyListeners();
  }

  void _onTokenRejected() {
    if (_stage == AuthStage.signedOut) return;
    _clearToken();
    unawaited(CampusImageCache.clear());
    _me = null;
    _stage = AuthStage.signedOut;
    notifyListeners();
  }

  Future<void> _clearToken() async {
    api.token = null;
    try {
      await _storage.delete(key: _tokenKey);
    } catch (_) {
      // Nothing useful to do if the keystore refuses the delete.
    }
  }
}
