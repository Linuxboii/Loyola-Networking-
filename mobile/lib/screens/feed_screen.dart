import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import '../widgets/post_card.dart';
import 'composer_screen.dart';
import 'groups_screen.dart';
import 'post_detail_screen.dart';
import 'profile_screen.dart';
import 'search_screen.dart';

/// The campus feed.
///
/// Two modes and nothing else: **Latest** is plain reverse-chronological, and
/// **For you** is a relevance sort — batch, department, your interests, how
/// recent. Neither optimises for time spent, which is the point.
class FeedScreen extends StatefulWidget {
  const FeedScreen({super.key});

  @override
  State<FeedScreen> createState() => _FeedScreenState();
}

class _FeedScreenState extends State<FeedScreen> {
  final _scroll = ScrollController();
  final List<Post> _posts = [];

  String _mode = 'latest';
  String? _tag;
  int _page = 1;
  bool _hasMore = true;
  bool _loading = false;
  bool _initialLoad = true;
  String? _error;
  List<String> _trending = const [];

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
    _refresh();
    _loadChrome();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (_scroll.position.pixels > _scroll.position.maxScrollExtent - 600) {
      _loadMore();
    }
  }

  Future<void> _loadChrome() async {
    try {
      final home = await context.read<Repository>().home();
      if (!mounted) return;
      setState(() {
        _trending = ((home['trending_tags'] as List?) ?? const [])
            .map((e) => '${(e as Map)['tag']}')
            .toList();
      });
    } catch (_) {
      // The tag rail is a nicety; its failure must not block the feed.
    }
  }

  Future<void> _refresh() async {
    setState(() {
      _error = null;
      _page = 1;
      _hasMore = true;
    });
    try {
      final page = await context.read<Repository>().feed(mode: _mode, tag: _tag, page: 1);
      if (!mounted) return;
      setState(() {
        _posts
          ..clear()
          ..addAll(page.items);
        _hasMore = page.hasMore;
        _initialLoad = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '$error';
        _initialLoad = false;
      });
    }
  }

  Future<void> _loadMore() async {
    if (_loading || !_hasMore || _initialLoad) return;
    setState(() => _loading = true);
    try {
      final next = _page + 1;
      final page = await context.read<Repository>().feed(mode: _mode, tag: _tag, page: next);
      if (!mounted) return;
      setState(() {
        _posts.addAll(page.items);
        _page = next;
        _hasMore = page.hasMore;
      });
    } catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _setMode(String mode) {
    if (_mode == mode) return;
    setState(() => _mode = mode);
    _scrollToTop();
    _refresh();
  }

  void _setTag(String? tag) {
    setState(() => _tag = tag);
    _scrollToTop();
    _refresh();
  }

  void _scrollToTop() {
    if (_scroll.hasClients) _scroll.jumpTo(0);
  }

  void _replace(Post updated) {
    final index = _posts.indexWhere((p) => p.id == updated.id);
    if (index >= 0) setState(() => _posts[index] = updated);
  }

  Future<void> _compose() async {
    final created = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => const ComposerScreen()),
    );
    if (created == true) {
      _scrollToTop();
      await _refresh();
    }
  }

  Future<void> _openPost(Post post) async {
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => PostDetailScreen(postId: post.id)),
    );
    if (changed == true) _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final canWrite = context.select<Session, bool>((s) => s.canWrite);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Campus'),
        actions: [
          IconButton(
            tooltip: 'Search',
            onPressed: () => Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => const SearchScreen())),
            icon: const Icon(Icons.search_rounded),
          ),
          IconButton(
            tooltip: 'Groups',
            onPressed: () => Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => const GroupsScreen())),
            icon: const Icon(Icons.groups_outlined),
          ),
        ],
        bottom: PreferredSize(
          preferredSize: Size.fromHeight(_tag == null ? 48 : 96),
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
                child: SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(
                      value: 'latest',
                      label: Text('Latest'),
                      icon: Icon(Icons.schedule_rounded, size: 17),
                    ),
                    ButtonSegment(
                      value: 'foryou',
                      label: Text('For you'),
                      icon: Icon(Icons.auto_awesome_outlined, size: 17),
                    ),
                  ],
                  selected: {_mode},
                  showSelectedIcon: false,
                  onSelectionChanged: (value) => _setMode(value.first),
                ),
              ),
              if (_tag != null)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          'Filtered by #$_tag',
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                      ),
                      TextButton.icon(
                        onPressed: () => _setTag(null),
                        icon: const Icon(Icons.close_rounded, size: 16),
                        label: const Text('Clear'),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ),
      floatingActionButton: canWrite
          ? FloatingActionButton.extended(
              onPressed: _compose,
              icon: const Icon(Icons.edit_outlined),
              label: const Text('Post'),
            )
          : null,
      body: RefreshIndicator(
        onRefresh: () async {
          await _refresh();
          await _loadChrome();
        },
        child: _body(canWrite),
      ),
    );
  }

  Widget _body(bool canWrite) {
    if (_initialLoad) return const SkeletonList();
    if (_error != null && _posts.isEmpty) {
      return ListView(children: [ErrorView(message: _error!, onRetry: _refresh)]);
    }
    if (_posts.isEmpty) {
      return ListView(
        children: [
          EmptyState(
            icon: Icons.forum_outlined,
            title: _tag == null ? 'Nothing here yet' : 'Nothing tagged #$_tag',
            message: _tag == null
                ? 'Be the first to post something to campus.'
                : 'Try another tag, or clear the filter.',
            action: canWrite && _tag == null
                ? FilledButton(onPressed: _compose, child: const Text('Write the first post'))
                : null,
          ),
        ],
      );
    }

    final hasRail = _trending.isNotEmpty && _tag == null;
    return ListView.separated(
      controller: _scroll,
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 96),
      itemCount: _posts.length + (hasRail ? 1 : 0) + 1,
      separatorBuilder: (_, __) => const SizedBox(height: 12),
      itemBuilder: (context, index) {
        if (hasRail && index == 0) return _TrendingRail(tags: _trending, onTap: _setTag);
        final postIndex = index - (hasRail ? 1 : 0);
        if (postIndex >= _posts.length) return _footer();

        final post = _posts[postIndex];
        return PostCard(
          post: post,
          canVote: canWrite,
          onTap: () => _openPost(post),
          onTagTap: _setTag,
          onAuthorTap: post.author.handle == null
              ? null
              : () => Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => ProfileScreen(handle: post.author.handle),
                    ),
                  ),
          onVoted: (score, myVote) => _replace(post.copyWith(score: score, myVote: myVote)),
          onPollVote: (optionId) async {
            try {
              final (options, mine) = await context.read<Repository>().votePoll(post.id, optionId);
              _replace(post.copyWith(poll: options, myPollOption: mine));
            } catch (error) {
              if (mounted) showError(context, error);
            }
          },
        );
      },
    );
  }

  Widget _footer() {
    if (_loading) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 24),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (_hasMore) return const SizedBox(height: 24);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 28),
      child: Center(
        child: Text(
          'You are all caught up',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ),
    );
  }
}

class _TrendingRail extends StatelessWidget {
  const _TrendingRail({required this.tags, required this.onTap});

  final List<String> tags;
  final void Function(String tag) onTap;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 34,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: tags.length,
        separatorBuilder: (_, __) => const SizedBox(width: 8),
        itemBuilder: (_, index) => TagChip(tag: tags[index], onTap: () => onTap(tags[index])),
      ),
    );
  }
}
