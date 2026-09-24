import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/updater.dart';

/// How an update is offered.
///
/// Two shapes, one body. A normal update is a sheet the student can swipe away;
/// a blocking one — this build is retired, or the release is mandatory — is the
/// same content in a dialog with no way out but installing. Both show the notes
/// the release was published with, because "what changed" is the only thing
/// that makes anyone tap Install.
class UpdatePrompt extends StatefulWidget {
  const UpdatePrompt({super.key, required this.status});

  final UpdateStatus status;

  /// Show the right kind of prompt for [status]. Returns when it closes.
  static Future<void> show(BuildContext context, UpdateStatus status) {
    final blocking = status.blocking;
    final child = UpdatePrompt(status: status);
    if (blocking) {
      return showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (_) => PopScope(
          canPop: false,
          child: Dialog(
            insetPadding:
                const EdgeInsets.symmetric(horizontal: 24, vertical: 40),
            child: child,
          ),
        ),
      );
    }
    return showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => child,
    );
  }

  @override
  State<UpdatePrompt> createState() => _UpdatePromptState();
}

class _UpdatePromptState extends State<UpdatePrompt> {
  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final updater = context.watch<Updater>();
    final status = widget.status;
    final release = status.latest;
    final blocking = status.blocking;

    // Retired with nothing to install is the one dead end: the server has
    // pulled the build but published no replacement.
    if (release == null) {
      return _Padded(
        children: [
          Text('Update required', style: theme.textTheme.titleLarge),
          const SizedBox(height: 8),
          Text(
            'This version of the app is no longer supported, and no newer build has been '
            'published yet. Ask on campus for the current APK, or use the website in the '
            'meantime.',
            style: theme.textTheme.bodyMedium,
          ),
        ],
      );
    }

    final failed = updater.stage == UpdateStage.failed;

    return _Padded(
      children: [
        Row(
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: scheme.primary.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Icon(Icons.system_update_rounded, color: scheme.primary),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    blocking ? 'Update required' : 'Update available',
                    style: theme.textTheme.titleLarge,
                  ),
                  Text(
                    'Version ${release.version} · build ${release.build}'
                    '${release.sizeLabel.isEmpty ? '' : ' · ${release.sizeLabel}'}',
                    style: theme.textTheme.bodySmall
                        ?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 14),
        if (status.unsupported)
          _Note(
            icon: Icons.lock_clock_rounded,
            color: scheme.error,
            text:
                'Your build (${status.currentLabel}) has been retired and can no longer '
                'talk to the campus server. Installing this update is the only way on.',
          )
        else if (blocking)
          _Note(
            icon: Icons.priority_high_rounded,
            color: scheme.error,
            text:
                'This one is required — the previous build has a problem that cannot be '
                'fixed from the server.',
          ),
        if (release.notes.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text(release.notes, style: theme.textTheme.bodyMedium),
        ],
        const SizedBox(height: 16),
        if (updater.stage == UpdateStage.downloading ||
            updater.stage == UpdateStage.verifying) ...[
          LinearProgressIndicator(
            value: updater.stage == UpdateStage.verifying
                ? null
                : updater.progress,
            minHeight: 6,
            borderRadius: BorderRadius.circular(999),
          ),
          const SizedBox(height: 8),
          Text(
            updater.stage == UpdateStage.verifying
                ? 'Checking the download…'
                : 'Downloading… ${(updater.progress * 100).round()}%',
            style: theme.textTheme.bodySmall
                ?.copyWith(color: scheme.onSurfaceVariant),
          ),
        ],
        if (failed && updater.error != null) ...[
          _Note(
              icon: Icons.error_outline_rounded,
              color: scheme.error,
              text: updater.error!),
          const SizedBox(height: 8),
        ],
        const SizedBox(height: 8),
        FilledButton(
          onPressed:
              updater.busy ? null : () => updater.downloadAndInstall(release),
          child: Text(failed ? 'Try again' : 'Install update'),
        ),
        if (!blocking) ...[
          const SizedBox(height: 8),
          TextButton(
            onPressed: updater.busy
                ? null
                : () async {
                    await context.read<Updater>().snooze(release);
                    if (context.mounted) Navigator.of(context).maybePop();
                  },
            child: const Text('Not now'),
          ),
        ],
        const SizedBox(height: 4),
        Text(
          'The download is checked against the signature the server published before '
          'Android is asked to install it.',
          textAlign: TextAlign.center,
          style: theme.textTheme.bodySmall
              ?.copyWith(color: scheme.onSurfaceVariant),
        ),
      ],
    );
  }
}

class _Padded extends StatelessWidget {
  const _Padded({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: children,
        ),
      ),
    );
  }
}

class _Note extends StatelessWidget {
  const _Note({required this.icon, required this.color, required this.text});

  final IconData icon;
  final Color color;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: color),
          const SizedBox(width: 10),
          Expanded(
            child: Text(text,
                style: Theme.of(context)
                    .textTheme
                    .bodySmall
                    ?.copyWith(height: 1.4)),
          ),
        ],
      ),
    );
  }
}

/// Runs one update check shortly after the app is up, and shows the prompt if
/// there is anything to say.
///
/// It wraps the app rather than living in a screen so the check survives the
/// sign-in / verification / feed transitions — a retired build has to be caught
/// even when the student never gets past the splash.
class UpdateGate extends StatefulWidget {
  const UpdateGate({super.key, required this.child});

  final Widget child;

  @override
  State<UpdateGate> createState() => _UpdateGateState();
}

class _UpdateGateState extends State<UpdateGate> {
  bool _showing = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _check());
  }

  Future<void> _check() async {
    if (_showing || !Updater.supported) return;
    final updater = context.read<Updater>();
    final status = await updater.check();
    if (status == null || !mounted) return;
    if (!await updater.shouldPrompt(status) || !mounted) return;

    _showing = true;
    try {
      await UpdatePrompt.show(context, status);
    } finally {
      _showing = false;
    }
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
