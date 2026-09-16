import 'package:flutter/material.dart';

class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Wordmark(size: 64),
            const SizedBox(height: 28),
            SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2.2, color: scheme.primary),
            ),
          ],
        ),
      ),
    );
  }
}

/// The app's mark: the college monogram in a rounded square.
class Wordmark extends StatelessWidget {
  const Wordmark({super.key, this.size = 48, this.showName = true});

  final double size;
  final bool showName;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: size,
          height: size,
          decoration: BoxDecoration(
            color: scheme.primary,
            borderRadius: BorderRadius.circular(size * 0.28),
          ),
          alignment: Alignment.center,
          child: Text(
            'LA',
            style: TextStyle(
              color: scheme.onPrimary,
              fontSize: size * 0.4,
              fontWeight: FontWeight.w800,
              letterSpacing: -0.5,
            ),
          ),
        ),
        if (showName) ...[
          SizedBox(height: size * 0.28),
          Text(
            'Loyola Networking',
            style: TextStyle(
              fontSize: size * 0.3,
              fontWeight: FontWeight.w700,
              letterSpacing: -0.5,
              color: scheme.onSurface,
            ),
          ),
        ],
      ],
    );
  }
}
