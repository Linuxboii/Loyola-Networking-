import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import 'event_detail_screen.dart';

class EventsScreen extends StatefulWidget {
  const EventsScreen({super.key});

  @override
  State<EventsScreen> createState() => _EventsScreenState();
}

class _EventsScreenState extends State<EventsScreen> {
  String _when = 'upcoming';
  List<CampusEvent> _events = const [];
  bool _initialLoad = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final events = await context.read<Repository>().events(when: _when);
      if (!mounted) return;
      setState(() {
        _events = events;
        _initialLoad = false;
      });
    } catch (error) {
      if (mounted) {
        setState(() {
          _error = '$error';
          _initialLoad = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final canHost = context.select<Session, bool>((s) => s.me?.can('host_event') ?? false);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Events'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(48),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'upcoming', label: Text('Upcoming')),
                ButtonSegment(value: 'past', label: Text('Past')),
              ],
              selected: {_when},
              showSelectedIcon: false,
              onSelectionChanged: (value) {
                setState(() {
                  _when = value.first;
                  _initialLoad = true;
                });
                _load();
              },
            ),
          ),
        ),
      ),
      floatingActionButton: canHost && _when == 'upcoming'
          ? FloatingActionButton.extended(
              onPressed: () async {
                final created = await Navigator.of(context)
                    .push<bool>(MaterialPageRoute(builder: (_) => const NewEventScreen()));
                if (created == true) _load();
              },
              icon: const Icon(Icons.add_rounded),
              label: const Text('Host'),
            )
          : null,
      body: RefreshIndicator(
        onRefresh: _load,
        child: _initialLoad
            ? const SkeletonList()
            : _error != null && _events.isEmpty
                ? ListView(children: [ErrorView(message: _error!, onRetry: _load)])
                : _events.isEmpty
                    ? ListView(
                        children: [
                          EmptyState(
                            icon: Icons.event_busy_outlined,
                            title: _when == 'upcoming' ? 'Nothing scheduled' : 'No past events',
                            message: _when == 'upcoming'
                                ? 'When a club posts an event it shows up here, with a ticket you can scan at the door.'
                                : 'Events move here once they are over.',
                          ),
                        ],
                      )
                    : ListView.separated(
                        padding: const EdgeInsets.fromLTRB(14, 12, 14, 96),
                        itemCount: _events.length,
                        separatorBuilder: (_, __) => const SizedBox(height: 10),
                        itemBuilder: (context, index) => _EventTile(
                          event: _events[index],
                          onTap: () async {
                            await Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) => EventDetailScreen(eventId: _events[index].id),
                              ),
                            );
                            _load();
                          },
                        ),
                      ),
      ),
    );
  }
}

class _EventTile extends StatelessWidget {
  const _EventTile({required this.event, required this.onTap});

  final CampusEvent event;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final start = event.startsAt.toLocal();

    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 54,
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: BoxDecoration(
                  color: scheme.primary.withValues(alpha: 0.10),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Column(
                  children: [
                    Text(
                      _month(start.month),
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 0.6,
                        color: scheme.primary,
                      ),
                    ),
                    Text(
                      '${start.day}',
                      style: TextStyle(
                        fontSize: 21,
                        fontWeight: FontWeight.w800,
                        height: 1.1,
                        color: scheme.primary,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            event.title,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.titleMedium,
                          ),
                        ),
                        if (event.isGoing)
                          Icon(Icons.check_circle_rounded, size: 18, color: scheme.secondary),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      eventDate(event.startsAt),
                      style: theme.textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                    if (event.venue.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Row(
                        children: [
                          Icon(Icons.place_outlined, size: 14, color: scheme.onSurfaceVariant),
                          const SizedBox(width: 4),
                          Expanded(
                            child: Text(
                              event.venue,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: theme.textTheme.bodySmall,
                            ),
                          ),
                        ],
                      ),
                    ],
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Text(
                          '${event.rsvpCount} going',
                          style: theme.textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600),
                        ),
                        if (event.capacity != null) ...[
                          const SizedBox(width: 6),
                          Text(
                            '· ${event.capacity} places',
                            style: theme.textTheme.bodySmall,
                          ),
                        ],
                        if (event.isOfficial) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                            decoration: BoxDecoration(
                              color: scheme.secondary.withValues(alpha: 0.16),
                              borderRadius: BorderRadius.circular(999),
                            ),
                            child: Text(
                              'CLUB',
                              style: TextStyle(
                                fontSize: 9.5,
                                fontWeight: FontWeight.w800,
                                letterSpacing: 0.6,
                                color: scheme.secondary,
                              ),
                            ),
                          ),
                        ],
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  static String _month(int month) => const [
        'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
        'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',
      ][month - 1];
}

/// Host a new event. Only members at Established standing reach this screen.
class NewEventScreen extends StatefulWidget {
  const NewEventScreen({super.key});

  @override
  State<NewEventScreen> createState() => _NewEventScreenState();
}

class _NewEventScreenState extends State<NewEventScreen> {
  final _title = TextEditingController();
  final _description = TextEditingController();
  final _venue = TextEditingController();
  final _capacity = TextEditingController();
  final _tags = TextEditingController();

  DateTime _start = DateTime.now().add(const Duration(days: 1, hours: 1));
  bool _sending = false;

  @override
  void dispose() {
    _title.dispose();
    _description.dispose();
    _venue.dispose();
    _capacity.dispose();
    _tags.dispose();
    super.dispose();
  }

  Future<void> _pickDateTime() async {
    final date = await showDatePicker(
      context: context,
      initialDate: _start,
      firstDate: DateTime.now(),
      lastDate: DateTime.now().add(const Duration(days: 365)),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(_start),
    );
    if (time == null) return;
    setState(() {
      _start = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    });
  }

  Future<void> _send() async {
    if (_title.text.trim().length < 3 || _sending) return;
    setState(() => _sending = true);
    try {
      await context.read<Repository>().createEvent(
            title: _title.text.trim(),
            description: _description.text.trim(),
            venue: _venue.text.trim(),
            startsAt: _start,
            capacity: int.tryParse(_capacity.text.trim()),
            tags: _tags.text
                .replaceAll(',', ' ')
                .split(RegExp(r'\s+'))
                .where((t) => t.trim().isNotEmpty)
                .toList(),
          );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (error) {
      if (mounted) {
        showError(context, error);
        setState(() => _sending = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Host an event'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: FilledButton(
              onPressed: _sending ? null : _send,
              style: FilledButton.styleFrom(minimumSize: const Size(72, 38)),
              child: const Text('Create'),
            ),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 40),
        children: [
          TextField(
            controller: _title,
            autofocus: true,
            textCapitalization: TextCapitalization.sentences,
            decoration: const InputDecoration(labelText: 'Event name'),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 14),
          ListTile(
            onTap: _pickDateTime,
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.schedule_rounded),
            title: const Text('Starts'),
            subtitle: Text(eventDate(_start)),
            trailing: const Icon(Icons.chevron_right_rounded),
          ),
          const Divider(),
          const SizedBox(height: 10),
          TextField(
            controller: _venue,
            decoration: const InputDecoration(
              labelText: 'Venue',
              prefixIcon: Icon(Icons.place_outlined),
            ),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: _description,
            minLines: 4,
            maxLines: null,
            textCapitalization: TextCapitalization.sentences,
            decoration: const InputDecoration(
              labelText: 'What is it about?',
              alignLabelWithHint: true,
            ),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: _capacity,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
              labelText: 'Capacity (optional)',
              helperText: 'RSVPs stop once it is full.',
              prefixIcon: Icon(Icons.people_outline_rounded),
            ),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: _tags,
            decoration: const InputDecoration(
              labelText: 'Tags',
              prefixIcon: Icon(Icons.tag_rounded),
            ),
          ),
          const SizedBox(height: 20),
          Text(
            'Everyone who RSVPs gets a ticket code. Scanning it at the door is what '
            'awards attendance reputation — so check people in rather than taking a '
            'paper list.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
