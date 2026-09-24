import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'core/api_client.dart';
import 'core/config.dart';
import 'core/session.dart';
import 'core/theme.dart';
import 'core/theme_controller.dart';
import 'core/updater.dart';
import 'data/repository.dart';
import 'screens/auth_screen.dart';
import 'screens/shell_screen.dart';
import 'screens/splash_screen.dart';
import 'screens/verify_screen.dart';
import 'widgets/update_prompt.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await AppConfig.load();
  final themes = ThemeController();
  await themes.load();
  final api = ApiClient();
  runApp(
    MultiProvider(
      providers: [
        Provider<ApiClient>.value(value: api),
        Provider<Repository>(create: (_) => Repository(api)),
        ChangeNotifierProvider<Session>(
            create: (_) => Session(api)..bootstrap()),
        ChangeNotifierProvider<Updater>(create: (_) => Updater()),
        ChangeNotifierProvider<ThemeController>.value(value: themes),
      ],
      child: const LoyolaApp(),
    ),
  );
}

class LoyolaApp extends StatelessWidget {
  const LoyolaApp({super.key});

  @override
  Widget build(BuildContext context) {
    final themes = context.watch<ThemeController>();
    return MaterialApp(
      title: 'Loyola Networking',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: themes.mode,
      // The update check sits above every stage of the app: a build old enough
      // to be refused by the server must be caught even at the splash screen.
      home: const UpdateGate(child: _Root()),
    );
  }
}

/// Routes on authentication state rather than on navigation history, so the
/// verification wall cannot be skipped by a back gesture or a deep link.
class _Root extends StatelessWidget {
  const _Root();

  @override
  Widget build(BuildContext context) {
    final stage = context.select<Session, AuthStage>((s) => s.stage);
    final child = switch (stage) {
      AuthStage.starting => const SplashScreen(),
      AuthStage.signedOut => const AuthScreen(),
      AuthStage.needsVerification => const VerifyScreen(),
      AuthStage.ready => const ShellScreen(),
    };
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 220),
      child: KeyedSubtree(key: ValueKey(stage), child: child),
    );
  }
}
