import 'package:shared_preferences/shared_preferences.dart';

/// The campus production host is the safe default for every released build.
/// Local development can still opt in with `--dart-define=API_BASE_URL=...`.
class AppConfig {
  static const String _compiledDefault = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'https://loyola.avlokai.com',
  );

  static const String _prefsKey = 'api_base_url';
  static String _baseUrl = _compiledDefault;

  static String get baseUrl => _baseUrl;
  static String get apiRoot => '$_baseUrl/api/v1';
  static String get compiledDefault => _compiledDefault;

  static bool _isLegacyLocal(String value) {
    final host = Uri.tryParse(value)?.host.toLowerCase() ?? '';
    return host == '10.0.2.2' || host == '127.0.0.1' || host == 'localhost' || host == '::1';
  }

  static Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getString(_prefsKey);
    if (stored != null && stored.isNotEmpty && !_isLegacyLocal(stored)) {
      _baseUrl = stored;
    } else if (stored != null && _isLegacyLocal(stored)) {
      await prefs.remove(_prefsKey);
    }
  }

  /// Reserved for the staff-only configuration screen.
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