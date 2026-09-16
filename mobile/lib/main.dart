import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'core/api_client.dart';
import 'core/session.dart';
import 'core/theme.dart';
import 'data/repository.dart';
import 'screens/auth_screen.dart';
import 'screens/shell_screen.dart';
import 'screens/splash_screen.dart';
import 'screens/verify_screen.dart';

void main() {
  final api = ApiClient();
  runApp(
    MultiProvider(
      providers: [
        Provider<ApiClient>.value(value: api),
        Provider<Repository>(create: (_) => Repository(api)),
        ChangeNotifierProvider<Session>(create: (_) => Session(api)..bootstrap()),
      ],
      child: const LoyolaApp(),
    ),
  );
}

class LoyolaApp extends StatelessWidget {
  const LoyolaApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Loyola Networking',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: ThemeMode.system,
      home: const _Root(),
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
