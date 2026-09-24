import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'dart:typed_data';

import '../core/session.dart';
import '../data/repository.dart';
import '../widgets/common.dart';

/// Permission-sensitive in-app controls. The server remains authoritative.
class AdminScreen extends StatefulWidget {
  const AdminScreen({super.key});

  @override
  State<AdminScreen> createState() => _AdminScreenState();
}

class _AdminScreenState extends State<AdminScreen> {
  List<Map<String, dynamic>> _queue = const [];
  List<Map<String, dynamic>> _members = const [];
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final repo = context.read<Repository>();
      final queue = await repo.verificationQueue();
      final members = await repo.managedMembers();
      if (mounted) {
        setState(() {
          _queue = queue;
          _members = members;
          _error = null;
        });
      }
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  Future<void> _decide(int id, String outcome) async {
    try {
      await context.read<Repository>().decideVerification(id, outcome);
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _reviewImages(Map<String, dynamic> item) async {
    final repo = context.read<Repository>();
    final images = <String, Uint8List>{};
    for (final kind in ['card', 'selfie', 'card_face']) {
      if (item['has_$kind'] == true) {
        try {
          images[kind] = Uint8List.fromList(
              await repo.verificationArtifact(item['id'] as int, kind));
        } catch (_) {}
      }
    }
    if (!mounted) return;
    await showDialog<void>(
        context: context,
        builder: (context) => Dialog.fullscreen(
                child: Scaffold(
              appBar:
                  AppBar(title: Text('Review @${item['handle']}'), actions: [
                IconButton(
                    onPressed: () => Navigator.pop(context),
                    icon: const Icon(Icons.close))
              ]),
              body: ListView(padding: const EdgeInsets.all(16), children: [
                Text('Extracted fields',
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 8),
                Text('${item['extracted'] ?? {}}'),
                const SizedBox(height: 16),
                for (final entry in images.entries) ...[
                  Text(entry.key.replaceAll('_', ' ').toUpperCase()),
                  const SizedBox(height: 8),
                  InteractiveViewer(
                      child: Image.memory(entry.value, fit: BoxFit.contain)),
                  const SizedBox(height: 24),
                ],
                if (images.isEmpty)
                  const Text(
                      'The encrypted images were already purged or are unavailable.'),
              ]),
            )));
  }

  Future<void> _setRole(
      Map<String, dynamic> member, String kind, bool value) async {
    try {
      final repo = context.read<Repository>();
      if (kind == 'moderator') {
        await repo.setModerator(member['id'] as int, value);
      } else {
        await repo.setOg(member['id'] as int, value);
      }
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _delegate(Map<String, dynamic> member) async {
    final repo = context.read<Repository>();
    final selected = <String>{};
    String duration = '24 hours';
    const actions = <String, String>{
      'approve_verification': 'Approve verification',
      'manage_moderators': 'Manage moderators',
      'manage_og': 'Manage OG badges',
    };
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (_, setDialogState) => AlertDialog(
          title: Text('Delegate to @${member['handle']}'),
          content: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Text(
                  'Choose the only actions this administrator may take.'),
              for (final entry in actions.entries)
                CheckboxListTile(
                  value: selected.contains(entry.key),
                  title: Text(entry.value),
                  contentPadding: EdgeInsets.zero,
                  onChanged: (checked) => setDialogState(() {
                    if (checked == true) {
                      selected.add(entry.key);
                    } else {
                      selected.remove(entry.key);
                    }
                  }),
                ),
              DropdownButtonFormField<String>(
                initialValue: duration,
                decoration: const InputDecoration(labelText: 'Access duration'),
                items: const [
                  DropdownMenuItem(value: '24 hours', child: Text('24 hours')),
                  DropdownMenuItem(value: '7 days', child: Text('7 days')),
                  DropdownMenuItem(
                      value: 'No expiry', child: Text('No expiry')),
                ],
                onChanged: (value) =>
                    setDialogState(() => duration = value ?? duration),
              ),
            ]),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(dialogContext, false),
                child: const Text('Cancel')),
            FilledButton(
              onPressed: selected.isEmpty
                  ? null
                  : () => Navigator.pop(dialogContext, true),
              child: const Text('Delegate'),
            ),
          ],
        ),
      ),
    );
    if (confirmed != true) return;
    final expiry = switch (duration) {
      '24 hours' => DateTime.now().add(const Duration(hours: 24)),
      '7 days' => DateTime.now().add(const Duration(days: 7)),
      _ => null,
    };
    try {
      await repo.grantAdmin(member['id'] as int, selected.toList(),
          expiresAt: expiry);
      if (!mounted) return;
      showMessage(context, 'Administrator access delegated.');
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final isSuperAdmin =
        context.read<Session>().me?.roles.contains('super_admin') ?? false;
    return Scaffold(
      appBar: AppBar(title: const Text('Community administration')),
      body: _error != null
          ? ErrorView(message: _error!, onRetry: _load)
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(children: [
                const SectionHeader(title: 'Pending verification'),
                if (_queue.isEmpty)
                  const ListTile(
                      title: Text('No verification requests waiting.')),
                for (final item in _queue)
                  ListTile(
                    title: Text(item['full_name'] as String),
                    subtitle: Text(
                        '@${item['handle']} · ${((item['confidence'] as num?) ?? 0).round()}% confidence'),
                    onTap: () => _reviewImages(item),
                    trailing: Wrap(spacing: 4, children: [
                      IconButton(
                          tooltip: 'Approve',
                          icon: const Icon(Icons.check_circle_outline),
                          onPressed: () =>
                              _decide(item['id'] as int, 'approve')),
                      IconButton(
                          tooltip: 'Reject',
                          icon: const Icon(Icons.cancel_outlined),
                          onPressed: () =>
                              _decide(item['id'] as int, 'reject')),
                    ]),
                  ),
                if (_queue.isNotEmpty)
                  const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 16),
                      child: Text(
                          'Tap a request to Review ID images and extracted fields.')),
                const SectionHeader(title: 'Members'),
                for (final member in _members)
                  ListTile(
                    title: Text(member['full_name'] as String),
                    subtitle: Text('@${member['handle']}'),
                    trailing: PopupMenuButton<String>(
                      onSelected: (value) {
                        if (value == 'grant') {
                          _delegate(member);
                          return;
                        }
                        _setRole(
                            member,
                            value,
                            !(value == 'moderator'
                                ? (member['roles'] as List)
                                    .contains('moderator')
                                : member['is_og'] == true));
                      },
                      itemBuilder: (_) => [
                        PopupMenuItem(
                            value: 'moderator',
                            child: Text(
                                (member['roles'] as List).contains('moderator')
                                    ? 'Remove moderator'
                                    : 'Make moderator')),
                        PopupMenuItem(
                            value: 'og',
                            child: Text(member['is_og'] == true
                                ? 'Remove OG'
                                : 'Make OG')),
                        if (isSuperAdmin)
                          const PopupMenuItem(
                              value: 'grant',
                              child: Text('Delegate admin access')),
                      ],
                    ),
                  ),
              ]),
            ),
    );
  }
}
