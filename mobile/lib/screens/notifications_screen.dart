import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import 'post_detail_screen.dart';
import 'question_detail_screen.dart';

class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key});

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  List<AppNotification> _items = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final items = await context.read<Repository>().notifications();
      if (!mounted) return;
      setState(() {
        _items = items;
        _loading = false;
      });
    } catch (error) {
      if (mounted) {
        setState(() {
          _error = '$error';
          _loading = false;
        });
      }
    }
  }

  Future<void> _markAllRead() async {
    try {
      await context.read<Repository>().markAllRead();
      if (!mounted) return;
      await context.read<Session>().refreshMe();
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  /// Notifications carry a web link (`/p/12`, `/qa/7`). Turn the ones the app
  /// has a screen for into a push, and ignore the rest rather than opening a
  /// browser that would ask the student to sign in again.
  Future<void> _open(AppNotification note) async {
    if (!note.read) {
      unawaited(context.read<Repository>().markRead(note.id));
      if (mounted) context.read<Session>().refreshUnread();
    }

    final link = note.link;
    final postMatch = RegExp(r'^/p/(\d+)').firstMatch(link);
    final qaMatch = RegExp(r'^/qa/(\d+)').firstMatch(link);

    if (postMatch != null) {
      await Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => PostDetailScreen(postId: int.parse(postMatch.group(1)!)),
        ),
      );
    } else if (qaMatch != null) {
      await Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => QuestionDetailScreen(questionId: int.parse(qaMatch.group(1)!)),
        ),
      );
    }
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final unreadCount = _items.where((n) => !n.read).length;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Alerts'),
        actions: [
          if (unreadCount > 0)
            TextButton(onPressed: _markAllRead, child: const Text('Mark all read')),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const SkeletonList(count: 5)
            : _error != null && _items.isEmpty
                ? ListView(children: [ErrorView(message: _error!, onRetry: _load)])
                : _items.isEmpty
                    ? ListView(
                        children: const [
                          EmptyState(
                            icon: Icons.notifications_none_rounded,
                            title: 'Nothing new',
                            message: 'Replies, accepted answers and reputation changes land here.',
                          ),
                        ],
                      )
                    : ListView.separated(
                        itemCount: _items.length,
                        separatorBuilder: (_, __) => const Divider(height: 1, indent: 68),
                        itemBuilder: (context, index) => _NotificationTile(
                          note: _items[index],
                          onTap: () => _open(_items[index]),
                        ),
                      ),
      ),
    );
  }
}

class _NotificationTile extends StatelessWidget {
  const _NotificationTile({required this.note, required this.onTap});

  final AppNotification note;
  final VoidCallback onTap;

  static const _icons = {
    'comment': Icons.mode_comment_outlined,
    'reply': Icons.reply_rounded,
    'answer': Icons.question_answer_outlined,
    'accepted': Icons.check_circle_outline_rounded,
    'reputation': Icons.trending_up_rounded,
    'verification': Icons.verified_user_outlined,
    'moderation': Icons.gavel_rounded,
    'endorsement': Icons.workspace_premium_outlined,
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;

    return ListTile(
      onTap: onTap,
      tileColor: note.read ? null : scheme.primary.withValues(alpha: 0.05),
      leading: CircleAvatar(
        backgroundColor: scheme.primary.withValues(alpha: 0.12),
        child: Icon(
          _icons[note.kind] ?? Icons.notifications_none_rounded,
          size: 19,
          color: scheme.primary,
        ),
      ),
      title: Text(
        note.title,
        style: TextStyle(
          fontSize: 14.5,
          fontWeight: note.read ? FontWeight.w500 : FontWeight.w700,
        ),
      ),
      subtitle: note.body.isEmpty
          ? null
          : Text(note.body, maxLines: 2, overflow: TextOverflow.ellipsis),
      trailing: Text(
        relativeTime(note.createdAt),
        style: theme.textTheme.bodySmall?.copyWith(fontSize: 11.5),
      ),
      isThreeLine: note.body.length > 60,
    );
  }
}
