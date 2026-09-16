import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import 'profile_screen.dart';

class EventDetailScreen extends StatefulWidget {
  const EventDetailScreen({super.key, required this.eventId});

  final int eventId;

  @override
  State<EventDetailScreen> createState() => _EventDetailScreenState();
}

class _EventDetailScreenState extends State<EventDetailScreen> {
  CampusEvent? _event;
  List<UserCard> _attendees = const [];
  bool _isHost = false;
  String? _error;
  bool _working = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final (event, attendees, isHost) =
          await context.read<Repository>().eventDetail(widget.eventId);
      if (!mounted) return;
      setState(() {
        _event = event;
        _attendees = attendees;
        _isHost = isHost;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  Future<void> _toggleRsvp() async {
    final event = _event;
    if (event == null || _working) return;
    setState(() => _working = true);
    try {
      await context
          .read<Repository>()
          .rsvp(event.id, state: event.isGoing ? 'cancelled' : 'going');
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => _working = false);
    }
  }

  Future<void> _checkIn() async {
    final controller = TextEditingController();
    final code = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Check in an attendee'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: controller,
              autofocus: true,
              textCapitalization: TextCapitalization.characters,
              decoration: const InputDecoration(labelText: 'Ticket code'),
            ),
            const SizedBox(height: 10),
            Text(
              'Read it from the attendee\'s ticket. Attendance reputation is awarded '
              'here and nowhere else.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('Check in'),
          ),
        ],
      ),
    );
    if (code == null || code.isEmpty || !mounted) return;
    try {
      final result = await context.read<Repository>().checkin(widget.eventId, code);
      if (!mounted) return;
      showMessage(context, '${result['message']}');
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final canWrite = context.select<Session, bool>((s) => s.canWrite);
    final event = _event;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Event'),
        actions: [
          if (_isHost)
            IconButton(
              tooltip: 'Check in',
              onPressed: _checkIn,
              icon: const Icon(Icons.qr_code_scanner_rounded),
            ),
        ],
      ),
      body: event == null
          ? (_error != null
              ? ErrorView(message: _error!, onRetry: _load)
              : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 40),
                children: [
                  Text(event.title, style: theme.textTheme.headlineSmall),
                  const SizedBox(height: 16),
                  _DetailRow(icon: Icons.schedule_rounded, text: eventDate(event.startsAt)),
                  if (event.venue.isNotEmpty)
                    _DetailRow(icon: Icons.place_outlined, text: event.venue),
                  _DetailRow(
                    icon: Icons.people_outline_rounded,
                    text: event.capacity == null
                        ? '${event.rsvpCount} going'
                        : '${event.rsvpCount} of ${event.capacity} places taken',
                  ),
                  if (event.checkinCount > 0)
                    _DetailRow(
                      icon: Icons.how_to_reg_outlined,
                      text: '${event.checkinCount} checked in',
                    ),
                  const SizedBox(height: 18),
                  AuthorLine(
                    user: event.host,
                    dense: true,
                    onTap: event.host.handle == null
                        ? null
                        : () => Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) => ProfileScreen(handle: event.host.handle),
                              ),
                            ),
                  ),
                  if (event.description.isNotEmpty) ...[
                    const SizedBox(height: 20),
                    Text(event.description, style: theme.textTheme.bodyLarge),
                  ],
                  if (event.tags.isNotEmpty) ...[
                    const SizedBox(height: 16),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: event.tags.map((tag) => TagChip(tag: tag)).toList(),
                    ),
                  ],
                  if (event.myTicketCode != null) ...[
                    const SizedBox(height: 24),
                    _Ticket(
                      code: event.myTicketCode!,
                      checkedIn: event.checkedInAt != null,
                    ),
                  ],
                  if (_attendees.isNotEmpty) ...[
                    const SizedBox(height: 28),
                    Text('Going', style: theme.textTheme.titleSmall),
                    const SizedBox(height: 12),
                    Wrap(
                      spacing: 10,
                      runSpacing: 10,
                      children: _attendees
                          .take(24)
                          .map(
                            (attendee) => InkWell(
                              onTap: attendee.handle == null
                                  ? null
                                  : () => Navigator.of(context).push(
                                        MaterialPageRoute(
                                          builder: (_) => ProfileScreen(handle: attendee.handle),
                                        ),
                                      ),
                              child: Avatar(user: attendee, radius: 18),
                            ),
                          )
                          .toList(),
                    ),
                  ],
                ],
              ),
            ),
      bottomNavigationBar: event == null || !canWrite
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: event.isGoing
                    ? OutlinedButton.icon(
                        onPressed: _working ? null : _toggleRsvp,
                        icon: const Icon(Icons.close_rounded, size: 18),
                        label: const Text('Cancel my RSVP'),
                      )
                    : FilledButton.icon(
                        onPressed: _working || event.isFull ? null : _toggleRsvp,
                        icon: const Icon(Icons.check_rounded),
                        label: Text(event.isFull ? 'Event is full' : 'I am going'),
                      ),
              ),
            ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        children: [
          Icon(icon, size: 18, color: scheme.onSurfaceVariant),
          const SizedBox(width: 10),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 14.5))),
        ],
      ),
    );
  }
}

class _Ticket extends StatelessWidget {
  const _Ticket({required this.code, required this.checkedIn});

  final String code;
  final bool checkedIn;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: checkedIn
            ? scheme.secondary.withValues(alpha: 0.12)
            : scheme.primary.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: (checkedIn ? scheme.secondary : scheme.primary).withValues(alpha: 0.4),
        ),
      ),
      child: Column(
        children: [
          Text(
            checkedIn ? 'CHECKED IN' : 'YOUR TICKET',
            style: TextStyle(
              fontSize: 11,
              letterSpacing: 1.4,
              fontWeight: FontWeight.w800,
              color: checkedIn ? scheme.secondary : scheme.primary,
            ),
          ),
          const SizedBox(height: 12),
          SelectableText(
            code,
            style: const TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.w800,
              letterSpacing: 3,
              fontFamily: 'monospace',
            ),
          ),
          const SizedBox(height: 10),
          TextButton.icon(
            onPressed: () {
              Clipboard.setData(ClipboardData(text: code));
              showMessage(context, 'Ticket code copied.');
            },
            icon: const Icon(Icons.copy_rounded, size: 16),
            label: const Text('Copy'),
          ),
          Text(
            'Show this at the door.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
