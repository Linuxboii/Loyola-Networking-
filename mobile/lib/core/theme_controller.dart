import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

enum AppThemeChoice { system, light, amoled }

class ThemeController extends ChangeNotifier {
  static const _key = 'theme_choice';
  AppThemeChoice _choice = AppThemeChoice.system;
  AppThemeChoice get choice => _choice;
  ThemeMode get mode => switch (_choice) {
        AppThemeChoice.light => ThemeMode.light,
        AppThemeChoice.amoled => ThemeMode.dark,
        AppThemeChoice.system => ThemeMode.system,
      };

  Future<void> load() async {
    final raw = (await SharedPreferences.getInstance()).getString(_key);
    _choice = AppThemeChoice.values.where((v) => v.name == raw).firstOrNull ??
        AppThemeChoice.system;
    notifyListeners();
  }

  Future<void> setChoice(AppThemeChoice value) async {
    _choice = value;
    notifyListeners();
    await (await SharedPreferences.getInstance()).setString(_key, value.name);
  }
}
