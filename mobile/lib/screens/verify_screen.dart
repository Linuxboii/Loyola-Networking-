import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';

/// The verification wall.
///
/// This is the screen the app exists for. Everything else can be done in a
/// browser; photographing an ID card is where a phone wins. The flow is
/// deliberately narrow — capture card, confirm what was read, capture selfie,
/// wait — because every extra choice here is a support ticket later.
class VerifyScreen extends StatefulWidget {
  const VerifyScreen({super.key});

  @override
  State<VerifyScreen> createState() => _VerifyScreenState();
}

class _VerifyScreenState extends State<VerifyScreen> {
  final _picker = ImagePicker();

  VerificationStatus? _status;
  String? _loadError;
  String? _actionError;
  bool _working = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loadError = null);
    try {
      final status = await context.read<Repository>().verificationStatus();
      if (!mounted) return;
      setState(() => _status = status);
      if (status.tier >= 2) await context.read<Session>().refreshMe();
    } catch (error) {
      if (mounted) setState(() => _loadError = '$error');
    }
  }

  Future<void> _capture({required bool card}) async {
    final source = await _chooseSource(card: card);
    if (source == null) return;

    final shot = await _picker.pickImage(
      source: source,
      // Full-resolution captures are 8 MB and slow to upload without helping the
      // OCR; 1800px on the long edge keeps the text crisp and the upload quick.
      maxWidth: 1800,
      maxHeight: 1800,
      imageQuality: 92,
      preferredCameraDevice: card ? CameraDevice.rear : CameraDevice.front,
    );
    if (shot == null || !mounted) return;

    setState(() {
      _working = true;
      _actionError = null;
    });
    try {
      final repo = context.read<Repository>();
      final result =
          card ? await repo.submitCard(shot.path) : await repo.submitSelfie(shot.path);
      if (!mounted) return;
      if (result['error'] != null) {
        setState(() => _actionError = '${result['error']}');
      }
      await _load();
      if (!mounted) return;
      await context.read<Session>().refreshMe();
    } catch (error) {
      if (mounted) setState(() => _actionError = error.toString());
    } finally {
      if (mounted) setState(() => _working = false);
    }
  }

  Future<ImageSource?> _chooseSource({required bool card}) => showModalBottomSheet<ImageSource>(
        context: context,
        showDragHandle: true,
        builder: (context) => SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              ListTile(
                leading: const Icon(Icons.photo_camera_outlined),
                title: Text(card ? 'Photograph the card now' : 'Take a selfie now'),
                subtitle: const Text('Recommended — a live capture verifies faster.'),
                onTap: () => Navigator.pop(context, ImageSource.camera),
              ),
              ListTile(
                leading: const Icon(Icons.photo_library_outlined),
                title: const Text('Choose an existing photo'),
                onTap: () => Navigator.pop(context, ImageSource.gallery),
              ),
              const SizedBox(height: 8),
            ],
          ),
        ),
      );

  Future<void> _dispute() async {
    final controller = TextEditingController();
    final note = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('What did we get wrong?'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLines: 3,
          maxLength: 500,
          decoration: const InputDecoration(
            hintText: 'My roll number is 21BCA1234, not 218CA1234.',
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('Send to reviewer'),
          ),
        ],
      ),
    );
    if (note == null || note.length < 3 || !mounted) return;
    try {
      await context.read<Repository>().disputeVerification(note);
      if (mounted) showMessage(context, 'Noted. A reviewer will see this with your card.');
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = context.watch<Session>();
    final status = _status;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Verify your ID'),
        actions: [
          IconButton(
            tooltip: 'Sign out',
            onPressed: () => session.logout(),
            icon: const Icon(Icons.logout_rounded),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loadError != null && status == null
            ? ListView(children: [ErrorView(message: _loadError!, onRetry: _load)])
            : status == null
                ? const Center(child: CircularProgressIndicator())
                : ListView(
                    padding: const EdgeInsets.fromLTRB(20, 8, 20, 40),
                    children: [
                      _StepTracker(step: status.nextStep),
                      const SizedBox(height: 24),
                      if (_actionError != null) ...[
                        _Notice(
                          icon: Icons.error_outline_rounded,
                          tone: _Tone.error,
                          title: 'That attempt did not pass',
                          body: _actionError!,
                        ),
                        const SizedBox(height: 16),
                      ],
                      ..._stepContent(status, session),
                      const SizedBox(height: 28),
                      _PrivacyNote(retentionDays: status.retentionDays),
                    ],
                  ),
      ),
    );
  }

  List<Widget> _stepContent(VerificationStatus status, Session session) {
    switch (status.nextStep) {
      case 'capture_selfie':
        return [
          const _Headline(
            title: 'Now a quick selfie',
            body: 'A reviewer compares it with the photo on your card. It is stored '
                'encrypted and deleted with the rest of your verification data.',
          ),
          const SizedBox(height: 20),
          if (status.extracted.isNotEmpty) _ExtractedCard(
            extracted: status.extracted,
            onDispute: _dispute,
          ),
          const SizedBox(height: 20),
          _PrimaryAction(
            label: 'Take selfie',
            icon: Icons.person_outline_rounded,
            busy: _working,
            onPressed: () => _capture(card: false),
          ),
        ];

      case 'wait_for_review':
        return [
          _Headline(
            title: 'With a reviewer now',
            body: status.queuePosition != null && status.queuePosition! > 0
                ? 'You are number ${status.queuePosition} in the queue. '
                    'Most cards are approved within a day.'
                : 'Most cards are approved within a day. You can read the network '
                    'meanwhile, but not post yet.',
          ),
          const SizedBox(height: 20),
          if (status.extracted.isNotEmpty)
            _ExtractedCard(extracted: status.extracted, onDispute: _dispute),
          const SizedBox(height: 20),
          if (session.me?.canRead == true)
            OutlinedButton.icon(
              onPressed: () => session.refreshMe(),
              icon: const Icon(Icons.refresh_rounded, size: 18),
              label: const Text('Check again'),
            ),
        ];

      case 'done':
        return [
          const _Headline(title: 'You are verified', body: 'Welcome in.'),
          const SizedBox(height: 20),
          FilledButton(
            onPressed: () => context.read<Session>().refreshMe(),
            child: const Text('Open the network'),
          ),
        ];

      default:
        return [
          const _Headline(
            title: 'Photograph your student ID',
            body: 'Lay the card flat, fill the frame, and keep the roll number and '
                'your name readable. We read the text on the card and never share '
                'the image with other students.',
          ),
          const SizedBox(height: 20),
          const _CaptureTips(),
          const SizedBox(height: 20),
          _PrimaryAction(
            label: 'Photograph ID card',
            icon: Icons.badge_outlined,
            busy: _working,
            onPressed: () => _capture(card: true),
          ),
          const SizedBox(height: 12),
          Center(
            child: Text(
              '${status.attemptsLeft} of ${status.attemptsAllowed} attempts left this week',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ];
    }
  }
}

class _StepTracker extends StatelessWidget {
  const _StepTracker({required this.step});

  final String step;

  static const _steps = ['capture_card', 'capture_selfie', 'wait_for_review', 'done'];
  static const _labels = ['ID card', 'Selfie', 'Review', 'Done'];

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final current = _steps.indexOf(step).clamp(0, _steps.length - 1);

    return Row(
      children: [
        for (var i = 0; i < _steps.length; i++) ...[
          Expanded(
            child: Column(
              children: [
                Container(
                  height: 4,
                  decoration: BoxDecoration(
                    color: i <= current ? scheme.primary : scheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(999),
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  _labels[i],
                  style: TextStyle(
                    fontSize: 11.5,
                    fontWeight: i == current ? FontWeight.w700 : FontWeight.w500,
                    color: i <= current ? scheme.primary : scheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
          if (i < _steps.length - 1) const SizedBox(width: 8),
        ],
      ],
    );
  }
}

class _Headline extends StatelessWidget {
  const _Headline({required this.title, required this.body});

  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: theme.textTheme.headlineSmall),
        const SizedBox(height: 10),
        Text(
          body,
          style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
      ],
    );
  }
}

class _CaptureTips extends StatelessWidget {
  const _CaptureTips();

  @override
  Widget build(BuildContext context) {
    const tips = [
      (Icons.wb_sunny_outlined, 'Even light, no flash glare across the text'),
      (Icons.crop_free_rounded, 'All four corners inside the frame'),
      (Icons.center_focus_strong_outlined, 'Hold still until the text is sharp'),
    ];
    final scheme = Theme.of(context).colorScheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        child: Column(
          children: [
            for (final (icon, text) in tips)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 10),
                child: Row(
                  children: [
                    Icon(icon, size: 19, color: scheme.primary),
                    const SizedBox(width: 12),
                    Expanded(child: Text(text, style: const TextStyle(fontSize: 13.5))),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _ExtractedCard extends StatelessWidget {
  const _ExtractedCard({required this.extracted, required this.onDispute});

  final Map<String, dynamic> extracted;
  final VoidCallback onDispute;

  static const _labels = {
    'roll_number': 'Roll number',
    'full_name': 'Name',
    'course': 'Course',
    'department': 'Department',
    'batch_year': 'Batch',
    'expiry': 'Card expiry',
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final rows = _labels.entries
        .where((entry) => extracted[entry.key] != null && '${extracted[entry.key]}'.isNotEmpty)
        .toList();
    if (rows.isEmpty) return const SizedBox.shrink();

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('What we read from your card', style: theme.textTheme.titleSmall),
            const SizedBox(height: 12),
            for (final entry in rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 108,
                      child: Text(
                        entry.value,
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ),
                    Expanded(
                      child: Text(
                        '${extracted[entry.key]}',
                        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                      ),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 4),
            TextButton.icon(
              onPressed: onDispute,
              icon: const Icon(Icons.flag_outlined, size: 17),
              label: const Text('Something is wrong'),
              style: TextButton.styleFrom(padding: EdgeInsets.zero),
            ),
          ],
        ),
      ),
    );
  }
}

enum _Tone { info, error }

class _Notice extends StatelessWidget {
  const _Notice({
    required this.icon,
    required this.title,
    required this.body,
    this.tone = _Tone.info,
  });

  final IconData icon;
  final String title;
  final String body;
  final _Tone tone;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final background = tone == _Tone.error ? scheme.errorContainer : scheme.surfaceContainerHighest;
    final foreground = tone == _Tone.error ? scheme.onErrorContainer : scheme.onSurface;

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(12)),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 20, color: foreground),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14, color: foreground),
                ),
                const SizedBox(height: 4),
                Text(
                  body,
                  style: TextStyle(fontSize: 13.5, height: 1.4, color: foreground),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _PrimaryAction extends StatelessWidget {
  const _PrimaryAction({
    required this.label,
    required this.icon,
    required this.busy,
    required this.onPressed,
  });

  final String label;
  final IconData icon;
  final bool busy;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) => FilledButton.icon(
        onPressed: busy ? null : onPressed,
        icon: busy
            ? const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2.2, color: Colors.white),
              )
            : Icon(icon),
        label: Text(busy ? 'Working…' : label),
      );
}

class _PrivacyNote extends StatelessWidget {
  const _PrivacyNote({required this.retentionDays});

  final int retentionDays;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: theme.colorScheme.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.lock_outline_rounded, size: 17, color: theme.colorScheme.onSurfaceVariant),
              const SizedBox(width: 8),
              Text('What happens to the photo', style: theme.textTheme.titleSmall),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'It is encrypted on the server, visible only to the reviewer who checks '
            'it, never shown to other students, and deleted automatically after '
            '$retentionDays days. Only your name, roll number, course and batch stay '
            'on your profile.',
            style: theme.textTheme.bodySmall?.copyWith(height: 1.5),
          ),
        ],
      ),
    );
  }
}
