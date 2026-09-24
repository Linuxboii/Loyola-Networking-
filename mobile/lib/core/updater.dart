import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:convert/convert.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:open_filex/open_filex.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'config.dart';

/// Keeping the app current without a store.
///
/// This network is for verified students of one college, so the app is not on
/// Play Store and nothing updates it for us. The server publishes builds (see
/// `app/services/releases.py`) and this class is the other half of that
/// contract: ask what the newest build is, download it, prove it is the file
/// the server described, and hand it to Android's package installer.
///
/// Three rules shape the behaviour, in order:
///
/// 1. A build below `min_supported_build` is *blocked*. It can no longer talk
///    to the API in a way we trust, so nagging is not enough.
/// 2. A mandatory release blocks too, but only until it is installed.
/// 3. Everything else is an offer the student can dismiss — and a dismissed
///    build stays dismissed, because a prompt that reappears every launch is
///    how people learn to tap "later" without reading.
///
/// Nothing here is authenticated. A build too old to sign in must still be able
/// to fix itself, which is the whole point of having an update path.

const String _kUpdatePath = '/api/v1/updates/android/latest';
const String _kSnoozedBuild = 'update_snoozed_build';
const String _kLastCheck = 'update_last_check_ms';

/// How long a quiet answer is trusted before we ask again on launch.
const Duration _kCheckInterval = Duration(hours: 6);

/// One published build, as the server describes it.
@immutable
class ReleaseInfo {
  const ReleaseInfo({
    required this.build,
    required this.version,
    required this.notes,
    required this.sizeBytes,
    required this.sha256,
    required this.downloadUrl,
    required this.mandatory,
    required this.minSupportedBuild,
  });

  final int build;
  final String version;
  final String notes;
  final int sizeBytes;
  final String sha256;

  /// Server-relative (`/api/v1/updates/…`), resolved against the configured
  /// base URL at download time so pointing the app at another server keeps
  /// updates coming from that same server.
  final String downloadUrl;
  final bool mandatory;
  final int minSupportedBuild;

  static ReleaseInfo? fromJson(Map<String, dynamic>? json) {
    if (json == null) return null;
    final build = json['build'];
    final url = json['download_url'];
    if (build is! int || url is! String || url.isEmpty) return null;
    return ReleaseInfo(
      build: build,
      version: '${json['version'] ?? ''}',
      notes: '${json['notes'] ?? ''}',
      sizeBytes: json['size'] is int ? json['size'] as int : 0,
      sha256: '${json['sha256'] ?? ''}',
      downloadUrl: url,
      mandatory: json['mandatory'] == true,
      minSupportedBuild: json['min_supported_build'] is int ? json['min_supported_build'] as int : 1,
    );
  }

  String get sizeLabel => sizeBytes <= 0
      ? ''
      : '${(sizeBytes / (1024 * 1024)).toStringAsFixed(1)} MB';
}

/// What a check found out.
@immutable
class UpdateStatus {
  const UpdateStatus({
    required this.currentBuild,
    required this.currentVersion,
    required this.latest,
    required this.minSupportedBuild,
  });

  final int currentBuild;
  final String currentVersion;
  final ReleaseInfo? latest;
  final int minSupportedBuild;

  bool get updateAvailable => latest != null && latest!.build > currentBuild;

  /// This build can no longer be used: the server has retired it.
  bool get unsupported => currentBuild < minSupportedBuild;

  /// The prompt cannot be dismissed — either we are retired, or the release
  /// itself is marked mandatory.
  bool get blocking => unsupported || (updateAvailable && latest!.mandatory);

  String get currentLabel => '$currentVersion ($currentBuild)';
}

/// Raised when a download finishes but is not the file the server described.
class UpdateVerificationException implements Exception {
  UpdateVerificationException(this.message);
  final String message;
  @override
  String toString() => message;
}

enum UpdateStage { idle, checking, downloading, verifying, ready, installing, failed }

class Updater extends ChangeNotifier {
  Updater({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  UpdateStage _stage = UpdateStage.idle;
  double _progress = 0;
  String? _error;
  UpdateStatus? _status;
  File? _downloaded;

  UpdateStage get stage => _stage;

  /// 0..1 while downloading, meaningless otherwise.
  double get progress => _progress;
  String? get error => _error;
  UpdateStatus? get status => _status;

  bool get busy =>
      _stage == UpdateStage.downloading ||
      _stage == UpdateStage.verifying ||
      _stage == UpdateStage.installing;

  /// Only Android has a sideloading story; everywhere else this is a no-op so
  /// the widgets above can stay platform-agnostic.
  static bool get supported => !kIsWeb && Platform.isAndroid;

  void _set(UpdateStage stage, {double? progress, String? error}) {
    _stage = stage;
    if (progress != null) _progress = progress;
    _error = error;
    notifyListeners();
  }

  /// Ask the server what the newest build is.
  ///
  /// Returns null when there is nothing to say — wrong platform, no network,
  /// nothing published, or we asked recently and the student is not asking now.
  /// A failed check is never surfaced as an error: the app works fine on the
  /// build it has, and a student who opened the feed did not ask about updates.
  Future<UpdateStatus?> check({bool force = false}) async {
    if (!supported) return null;

    final prefs = await SharedPreferences.getInstance();
    if (!force) {
      final last = prefs.getInt(_kLastCheck) ?? 0;
      final age = DateTime.now().millisecondsSinceEpoch - last;
      if (age < _kCheckInterval.inMilliseconds) return _status;
    }

    _set(UpdateStage.checking);
    try {
      final info = await PackageInfo.fromPlatform();
      final currentBuild = int.tryParse(info.buildNumber) ?? 0;
      final uri = Uri.parse('${AppConfig.baseUrl}$_kUpdatePath')
          .replace(queryParameters: {'build': '$currentBuild'});

      final response = await _client
          .get(uri, headers: {'Accept': 'application/json'})
          .timeout(const Duration(seconds: 12));
      if (response.statusCode != 200) {
        _set(UpdateStage.idle);
        return _status;
      }

      final body = _decode(response.body);
      if (body == null) {
        _set(UpdateStage.idle);
        return _status;
      }

      final status = UpdateStatus(
        currentBuild: currentBuild,
        currentVersion: info.version,
        latest: ReleaseInfo.fromJson(body['latest'] as Map<String, dynamic>?),
        minSupportedBuild:
            body['min_supported_build'] is int ? body['min_supported_build'] as int : 1,
      );

      await prefs.setInt(_kLastCheck, DateTime.now().millisecondsSinceEpoch);
      _status = status;
      _set(UpdateStage.idle);
      return status;
    } on Object {
      // Offline, DNS, a server mid-restart: all the same non-event.
      _set(UpdateStage.idle);
      return _status;
    }
  }

  /// True when this status is worth interrupting the student for.
  Future<bool> shouldPrompt(UpdateStatus status) async {
    if (!status.updateAvailable && !status.unsupported) return false;
    if (status.blocking) return true;
    final prefs = await SharedPreferences.getInstance();
    return (prefs.getInt(_kSnoozedBuild) ?? 0) < status.latest!.build;
  }

  /// Remember that this build was declined, so we stop asking about it.
  Future<void> snooze(ReleaseInfo release) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_kSnoozedBuild, release.build);
  }

  /// Download, verify, then install. Progress arrives through [progress].
  ///
  /// The APK lands in the app's own cache directory, which no other app can
  /// read, and is replaced rather than appended to if a previous attempt died
  /// halfway.
  Future<void> downloadAndInstall(ReleaseInfo release) async {
    if (busy) return;
    _set(UpdateStage.downloading, progress: 0, error: null);
    try {
      final file = await _download(release);
      _downloaded = file;
      _set(UpdateStage.ready, progress: 1);
      await install();
    } on UpdateVerificationException catch (error) {
      _set(UpdateStage.failed, error: error.message);
    } on TimeoutException {
      _set(UpdateStage.failed, error: 'The download timed out. Try again on a better connection.');
    } on Object catch (error) {
      _set(UpdateStage.failed, error: 'Could not download the update ($error).');
    }
  }

  /// Hand the downloaded APK to Android.
  ///
  /// The installer takes over from here: the student sees the system's own
  /// "update this app?" screen, which is the only thing that can actually write
  /// the new version. If they decline, nothing changes and the file stays put
  /// for the next attempt.
  Future<void> install() async {
    final file = _downloaded;
    if (file == null || !await file.exists()) {
      _set(UpdateStage.failed, error: 'The downloaded file went missing. Try again.');
      return;
    }
    _set(UpdateStage.installing);
    final result = await OpenFilex.open(
      file.path,
      type: 'application/vnd.android.package-archive',
    );
    if (result.type == ResultType.done) {
      _set(UpdateStage.ready);
    } else {
      // Almost always "install unknown apps" being switched off for us.
      _set(
        UpdateStage.failed,
        error: 'Android would not open the installer (${result.message}). '
            'Allow installing apps from this app in Settings, then tap Install again.',
      );
    }
  }

  Future<File> _download(ReleaseInfo release) async {
    final url = AppConfig.absolute(release.downloadUrl);
    if (url == null) throw UpdateVerificationException('The update has no download address.');

    final dir = Directory('${(await getTemporaryDirectory()).path}/updates');
    await dir.create(recursive: true);
    final file = File('${dir.path}/loyola-${release.build}.apk');
    if (await file.exists()) await file.delete();

    final request = http.Request('GET', Uri.parse(url));
    final response = await _client.send(request).timeout(const Duration(seconds: 30));
    if (response.statusCode != 200) {
      throw UpdateVerificationException('The server refused the download (${response.statusCode}).');
    }

    final total = response.contentLength ?? release.sizeBytes;
    var received = 0;
    final digest = AccumulatorSink<Digest>();
    final hasher = sha256.startChunkedConversion(digest);
    final sink = file.openWrite();

    try {
      await for (final chunk in response.stream) {
        sink.add(chunk);
        hasher.add(chunk);
        received += chunk.length;
        if (total > 0) {
          final next = received / total;
          // Repainting per 8 KB chunk is wasted frames; a percent is plenty.
          if (next - _progress >= 0.01 || next >= 1) {
            _progress = next.clamp(0, 1).toDouble();
            notifyListeners();
          }
        }
      }
    } finally {
      await sink.close();
      hasher.close();
    }

    _set(UpdateStage.verifying, progress: 1);

    if (release.sizeBytes > 0 && received != release.sizeBytes) {
      await file.delete();
      throw UpdateVerificationException(
        'The download stopped early. Check your connection and try again.',
      );
    }

    final actual = digest.events.single.toString();
    if (release.sha256.isNotEmpty && actual.toLowerCase() != release.sha256.toLowerCase()) {
      // Either the file was corrupted in transit or it is not the file the
      // server published. Both mean: do not install it.
      await file.delete();
      throw UpdateVerificationException(
        'The update did not match the signature the server published, so it was discarded.',
      );
    }
    return file;
  }

  Map<String, dynamic>? _decode(String body) {
    if (body.isEmpty) return null;
    try {
      final decoded = jsonDecode(body);
      return decoded is Map<String, dynamic> ? decoded : null;
    } on FormatException {
      return null;
    }
  }

  @override
  void dispose() {
    _client.close();
    super.dispose();
  }
}
