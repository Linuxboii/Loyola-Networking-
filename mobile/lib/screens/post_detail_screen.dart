import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import '../widgets/post_card.dart';
import '../widgets/vote_bar.dart';
import 'profile_screen.dart';

/// A post with its comment thread.
class PostDetailScreen extends StatefulWidget {
  const PostDetailScreen({super.key, required this.postId});

  final int postId;

  @override
  State<PostDetailScreen> createState() => _PostDetailScreenState();
}

class _PostDetailScreenState extends State<PostDetailScreen> {
  final _reply = TextEditingController();
  final _replyFocus = FocusNode();

  Post? _post;
  List<Comment> _comments = const [];
  String? _error;
  bool _sending = false;
  bool _asModerator = false;
  bool _changed = false;
  Comment? _replyingTo;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _reply.dispose();
    _replyFocus.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final (post, comments) = await context.read<Repository>().postDetail(widget.postId);
      if (!mounted) return;
      setState(() {
        _post = post;
        _comments = comments;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  Future<void> _submitReply() async {
    final body = _reply.text.trim();
    if (body.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      final comment = await context.read<Repository>().comment(
            widget.postId,
            body,
            parentId: _replyingTo?.id,
            asModerator: _asModerator,
          );
      if (!mounted) return;
      setState(() {
        _comments = [..._comments, comment];
        _post = _post?.copyWith(commentCount: (_post?.commentCount ?? 0) + 1);
        _reply.clear();
        _replyingTo = null;
        _changed = true;
      });
      _replyFocus.unfocus();
    } catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _deletePost() async {
    final confirmed = await _confirm(
      'Delete this post?',
      'It disappears from the feed and any reputation it earned is reversed.',
    );
    if (!confirmed || !mounted) return;
    try {
      await context.read<Repository>().deletePost(widget.postId);
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _deleteComment(Comment comment) async {
    final confirmed = await _confirm('Delete this comment?', 'This cannot be undone.');
    if (!confirmed || !mounted) return;
    try {
      await context.read<Repository>().deleteComment(comment.id);
      if (!mounted) return;
      setState(() {
        _comments = _comments.where((c) => c.id != comment.id).toList();
        _changed = true;
      });
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<bool> _confirm(String title, String body) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Text(body),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              style: FilledButton.styleFrom(
                backgroundColor: Theme.of(context).colorScheme.error,
              ),
              child: const Text('Delete'),
            ),
          ],
        ),
      ) ??
      false;

  Future<void> _report(String targetType, int targetId) async {
    const reasons = ['spam', 'harassment', 'misinformation', 'off-topic', 'other'];
    final reason = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text('Report to moderators', style: Theme.of(context).textTheme.titleMedium),
              ),
            ),
            for (final reason in reasons)
              ListTile(
                title: Text(reason[0].toUpperCase() + reason.substring(1)),
                onTap: () => Navigator.pop(context, reason),
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
    if (reason == null || !mounted) return;
    try {
      await context.read<Repository>().report(targetType, targetId, reason, '');
      if (mounted) showMessage(context, 'Reported. Moderators will review it.');
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = context.watch<Session>();
    final canWrite = session.canWrite;
    final post = _post;
    final myId = session.me?.card.id;

    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.of(context).pop(_changed);
      },
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Post'),
          actions: [
            if (post != null)
              PopupMenuButton<String>(
                onSelected: (value) {
                  if (value == 'delete') _deletePost();
                  if (value == 'report') _report('post', post.id);
                },
                itemBuilder: (context) => [
                  if (post.author.id == myId || (session.me?.card.isModerator ?? false))
                    const PopupMenuItem(value: 'delete', child: Text('Delete post')),
                  if (post.author.id != myId)
                    const PopupMenuItem(value: 'report', child: Text('Report to moderators')),
                ],
              ),
          ],
        ),
        body: post == null
            ? (_error != null
                ? ErrorView(message: _error!, onRetry: _load)
                : const Center(child: CircularProgressIndicator()))
            : RefreshIndicator(
                onRefresh: _load,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(14, 12, 14, 24),
                  children: [
                    PostCard(
                      post: post,
                      canVote: canWrite,
                      showFullBody: true,
                      onAuthorTap: post.author.handle == null
                          ? null
                          : () => Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (_) => ProfileScreen(handle: post.author.handle),
                                ),
                              ),
                      onVoted: (score, myVote) {
                        setState(() {
                          _post = post.copyWith(score: score, myVote: myVote);
                          _changed = true;
                        });
                      },
                      onPollVote: (optionId) async {
                        try {
                          final (options, mine) =
                              await context.read<Repository>().votePoll(post.id, optionId);
                          setState(() => _post = post.copyWith(poll: options, myPollOption: mine));
                        } catch (error) {
                          if (mounted) showError(context, error);
                        }
                      },
                    ),
                    const SizedBox(height: 20),
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      child: Text(
                        _comments.isEmpty
                            ? 'No replies yet'
                            : '${_comments.length} ${_comments.length == 1 ? 'reply' : 'replies'}',
                        style: Theme.of(context).textTheme.titleSmall,
                      ),
                    ),
                    const SizedBox(height: 8),
                    for (final comment in _rootFirst())
                      _CommentTile(
                        comment: comment,
                        depth: comment.parentId == null ? 0 : 1,
                        canVote: canWrite,
                        isMine: comment.author.id == myId,
                        onReply: canWrite
                            ? () {
                                setState(() => _replyingTo = comment);
                                _replyFocus.requestFocus();
                              }
                            : null,
                        onDelete: () => _deleteComment(comment),
                        onReport: () => _report('comment', comment.id),
                        onAuthorTap: comment.author.handle == null
                            ? null
                            : () => Navigator.of(context).push(
                                  MaterialPageRoute(
                                    builder: (_) => ProfileScreen(handle: comment.author.handle),
                                  ),
                                ),
                      ),
                  ],
                ),
              ),
        bottomNavigationBar: canWrite ? _replyBar(session.me?.roles.contains('moderator') ?? false) : null,
      ),
    );
  }

  /// Parents first, each with its direct replies beneath — one level of nesting
  /// is enough for a campus thread and keeps the layout readable on a phone.
  List<Comment> _rootFirst() {
    final roots = _comments.where((c) => c.parentId == null).toList();
    final ordered = <Comment>[];
    for (final root in roots) {
      ordered.add(root);
      ordered.addAll(_comments.where((c) => c.parentId == root.id));
    }
    // Anything whose parent was removed still deserves to be shown.
    final seen = ordered.map((c) => c.id).toSet();
    ordered.addAll(_comments.where((c) => !seen.contains(c.id)));
    return ordered;
  }

  Widget _replyBar(bool canUseModerator) {
    final scheme = Theme.of(context).colorScheme;
    return SafeArea(
      child: Container(
        decoration: BoxDecoration(
          color: scheme.surface,
          border: Border(top: BorderSide(color: scheme.outlineVariant)),
        ),
        padding: const EdgeInsets.fromLTRB(14, 8, 8, 8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (_replyingTo != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    Icon(Icons.reply_rounded, size: 15, color: scheme.onSurfaceVariant),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        'Replying to ${_replyingTo!.author.fullName}',
                        style: TextStyle(fontSize: 12.5, color: scheme.onSurfaceVariant),
                      ),
                    ),
                    IconButton(
                      onPressed: () => setState(() => _replyingTo = null),
                      icon: const Icon(Icons.close_rounded, size: 16),
                      visualDensity: VisualDensity.compact,
                    ),
                  ],
                ),
              ),
            Row(
              children: [
                if (canUseModerator) IconButton(
                  tooltip: _asModerator ? 'Reply as Moderator' : 'Reply as me',
                  onPressed: () => setState(() => _asModerator = !_asModerator),
                  icon: Icon(_asModerator ? Icons.shield_outlined : Icons.person_outline_rounded),
                ),
                Expanded(
                  child: TextField(
                    controller: _reply,
                    focusNode: _replyFocus,
                    minLines: 1,
                    maxLines: 4,
                    textCapitalization: TextCapitalization.sentences,
                    decoration: const InputDecoration(
                      hintText: 'Add a reply…',
                      contentPadding: EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    ),
                  ),
                ),
                IconButton.filled(
                  onPressed: _sending ? null : _submitReply,
                  icon: _sending
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                        )
                      : const Icon(Icons.send_rounded, size: 19),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _CommentTile extends StatelessWidget {
  const _CommentTile({
    required this.comment,
    required this.depth,
    required this.canVote,
    required this.isMine,
    required this.onDelete,
    required this.onReport,
    this.onReply,
    this.onAuthorTap,
  });

  final Comment comment;
  final int depth;
  final bool canVote;
  final bool isMine;
  final VoidCallback onDelete;
  final VoidCallback onReport;
  final VoidCallback? onReply;
  final VoidCallback? onAuthorTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: EdgeInsets.only(left: depth * 24.0, top: 4, bottom: 4),
      child: Container(
        padding: const EdgeInsets.fromLTRB(12, 10, 4, 4),
        decoration: BoxDecoration(
          color: theme.cardTheme.color,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: theme.colorScheme.outlineVariant.withValues(alpha: 0.6)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AuthorLine(
              user: comment.author,
              timestamp: comment.createdAt,
              dense: true,
              onTap: onAuthorTap,
              trailing: PopupMenuButton<String>(
                icon: const Icon(Icons.more_horiz_rounded, size: 18),
                iconSize: 18,
                onSelected: (value) => value == 'delete' ? onDelete() : onReport(),
                itemBuilder: (context) => [
                  if (isMine) const PopupMenuItem(value: 'delete', child: Text('Delete')),
                  if (!isMine) const PopupMenuItem(value: 'report', child: Text('Report')),
                ],
              ),
            ),
            const SizedBox(height: 8),
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: Text(comment.body, style: theme.textTheme.bodyMedium),
            ),
            Row(
              children: [
                VoteBar(
                  targetType: 'comment',
                  targetId: comment.id,
                  score: comment.score,
                  myVote: comment.myVote,
                  canVote: canVote,
                  horizontal: true,
                ),
                if (onReply != null && depth == 0)
                  TextButton(
                    onPressed: onReply,
                    style: TextButton.styleFrom(
                      foregroundColor: theme.colorScheme.onSurfaceVariant,
                      minimumSize: const Size(0, 32),
                      textStyle: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600),
                    ),
                    child: const Text('Reply'),
                  ),
                const Spacer(),
                Padding(
                  padding: const EdgeInsets.only(right: 10),
                  child: Text(
                    relativeTime(comment.createdAt),
                    style: theme.textTheme.bodySmall?.copyWith(fontSize: 11.5),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
