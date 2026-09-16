import 'package:shared_preferences/shared_preferences.dart';

/// Where the app talks to.
///
/// The compile-time default suits a developer running the server on their own
/// machine (10.0.2.2 is how an Android emulator reaches the host's localhost).
/// A pilot deployment is built with `--dart-define=API_BASE_URL=https://…`, and
/// the sign-in screen still lets a tester point at another server, because
/// during a campus rollout the address changes more often than the app does.
class AppConfig {
  static const String _compiledDefault = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8011',
  );

  static const String _prefsKey = 'api_base_url';

  static String _baseUrl = _compiledDefault;

  static String get baseUrl => _baseUrl;
  static String get apiRoot => '$_baseUrl/api/v1';
  static String get compiledDefault => _compiledDefault;

  static Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getString(_prefsKey);
    if (stored != null && stored.isNotEmpty) {
      _baseUrl = stored;
    }
  }

  static Future<void> setBaseUrl(String value) async {
    var cleaned = value.trim();
    while (cleaned.endsWith('/')) {
      cleaned = cleaned.substring(0, cleaned.length - 1);
    }
    if (cleaned.isEmpty) return;
    _baseUrl = cleaned;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKey, cleaned);
  }

  /// Media paths come back from the API as `/media/<signed token>`; the app
  /// needs them absolute.
  static String? absolute(String? path) {
    if (path == null || path.isEmpty) return null;
    if (path.startsWith('http://') || path.startsWith('https://')) return path;
    return '$_baseUrl$path';
  }
}
