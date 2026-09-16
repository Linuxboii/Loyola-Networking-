import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../core/theme.dart';
import '../data/repository.dart';
import '../widgets/common.dart';

/// Your standing, and every event that produced it.
///
/// The ledger is shown in full on purpose: a reputation number nobody can audit
/// is just a score, and students are right not to trust one.
class ReputationScreen extends StatefulWidget {
  const ReputationScreen({super.key});

  @override
  State<ReputationScreen> createState() => _ReputationScreenState();
}

class _ReputationScreenState extends State<ReputationScreen> {
  Map<String, dynamic>? _data;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await context.read<Repository>().reputation();
      if (mounted) setState(() => _data = data);
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final data = _data;

    return Scaffold(
      appBar: AppBar(title: const Text('Your standing')),
      body: data == null
          ? (_error != null
              ? ErrorView(message: _error!, onRetry: _load)
              : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.only(bottom: 40),
                children: [
                  Padding(
                    padding: const EdgeInsets.all(16),
                    child: Container(
                      padding: const EdgeInsets.all(20),
                      decoration: BoxDecoration(
                        color: tierColor('${data['tier']}', scheme).withValues(alpha: 0.10),
                        borderRadius: BorderRadius.circular(18),
                      ),
                      child: Column(
                        children: [
                          Text(
                            '${data['total']}',
                            style: theme.textTheme.displaySmall?.copyWith(
                              fontWeight: FontWeight.w800,
                              color: tierColor('${data['tier']}', scheme),
                            ),
                          ),
                          const SizedBox(height: 4),
                          TierBadge(tier: '${data['tier']}'),
                          if (data['next_tier'] != null) ...[
                            const SizedBox(height: 14),
                            _NextTier(
                              total: (data['total'] as num).toDouble(),
                              floor: (data['floor'] as num?)?.toDouble() ?? 0,
                              next: (data['next_tier'] as Map).cast<String, dynamic>(),
                            ),
                          ],
                          if (data['frozen'] == true) ...[
                            const SizedBox(height: 12),
                            Text(
                              'Your standing is frozen while moderators review reports.',
                              textAlign: TextAlign.center,
                              style: theme.textTheme.bodySmall?.copyWith(color: scheme.error),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                  const SectionHeader(title: 'Where it came from'),
                  for (final pillar in (data['pillars'] as List).cast<Map>())
                    ListTile(
                      dense: true,
                      title: Text('${pillar['label']}'),
                      trailing: Text(
                        '${pillar['value']}',
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                    ),
                  const SectionHeader(title: 'Ledger'),
                  if ((data['events'] as List).isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Text(
                        'Nothing yet. Answer a question, ship a project, or volunteer at an '
                        'event and it shows up here.',
                        style: theme.textTheme.bodySmall,
                      ),
                    ),
                  for (final event in (data['events'] as List).cast<Map>())
                    _LedgerRow(event: event.cast<String, dynamic>()),
                  const SizedBox(height: 20),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    child: Text(
                      'Points settle after a couple of days so a burst of votes cannot be '
                      'gamed, and they decay slowly if you go quiet. Every entry above is '
                      'permanent — nothing is edited after the fact.',
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                ],
              ),
            ),
    );
  }
}

class _NextTier extends StatelessWidget {
  const _NextTier({required this.total, required this.floor, required this.next});

  final double total;
  final double floor;
  final Map<String, dynamic> next;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final target = (next['at'] as num).toDouble();
    final span = (target - floor).clamp(1, double.infinity);
    final progress = ((total - floor) / span).clamp(0.0, 1.0);

    return Column(
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(999),
          child: LinearProgressIndicator(
            value: progress,
            minHeight: 8,
            backgroundColor: theme.colorScheme.surfaceContainerHighest,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          '${(target - total).toStringAsFixed(0)} to ${next['name']}',
          style: theme.textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600),
        ),
      ],
    );
  }
}

class _LedgerRow extends StatelessWidget {
  const _LedgerRow({required this.event});

  final Map<String, dynamic> event;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final points = (event['points'] as num?)?.toDouble() ?? 0;
    final pending = event['state'] == 'provisional';
    final positive = points >= 0;

    return ListTile(
      dense: true,
      title: Text(
        _humanise('${event['reason']}'),
        style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
      ),
      subtitle: Text(
        [
          if ((event['detail'] ?? '').toString().isNotEmpty) '${event['detail']}',
          relativeTime(DateTime.tryParse('${event['created_at']}')),
          if (pending) 'settling',
        ].join(' · '),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: Text(
        '${positive ? '+' : ''}${points.toStringAsFixed(points.abs() < 10 ? 1 : 0)}',
        style: TextStyle(
          fontWeight: FontWeight.w800,
          color: pending
              ? theme.colorScheme.onSurfaceVariant
              : positive
                  ? theme.colorScheme.primary
                  : theme.colorScheme.error,
        ),
      ),
    );
  }

  static String _humanise(String reason) {
    final words = reason.replaceAll('_', ' ').trim();
    if (words.isEmpty) return 'Adjustment';
    return words[0].toUpperCase() + words.substring(1);
  }
}
