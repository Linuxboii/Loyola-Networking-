import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import '../widgets/post_card.dart';
import 'composer_screen.dart';
import 'post_detail_screen.dart';
import 'profile_screen.dart';

class GroupsScreen extends StatefulWidget {
  const GroupsScreen({super.key});

  @override
  State<GroupsScreen> createState() => _GroupsScreenState();
}

class _GroupsScreenState extends State<GroupsScreen> {
  List<Group> _groups = const [];
  String? _kind;
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
      final groups = await context.read<Repository>().groups(kind: _kind);
      if (!mounted) return;
      setState(() {
        _groups = groups;
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

  Future<void> _create() async {
    final nameController = TextEditingController();
    final descriptionController = TextEditingController();
    var kind = 'interest';

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('Create community'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: nameController,
                autofocus: true,
                decoration: const InputDecoration(labelText: 'Name'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: descriptionController,
                maxLines: 3,
                decoration: const InputDecoration(labelText: 'What is it for?'),
              ),
              const SizedBox(height: 12),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'interest', label: Text('Interest')),
                  ButtonSegment(value: 'club', label: Text('Club')),
                ],
                selected: {kind},
                showSelectedIcon: false,
                onSelectionChanged: (value) => setDialogState(() => kind = value.first),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Create')),
          ],
        ),
      ),
    );

    if (confirmed != true || nameController.text.trim().length < 3 || !mounted) return;
    try {
      await context
          .read<Repository>()
          .createGroup(nameController.text.trim(), descriptionController.text.trim(), kind);
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final canCreate = context.select<Session, bool>((s) => s.canWrite);
    final mine = _groups.where((g) => g.joined).toList();
    final others = _groups.where((g) => !g.joined).toList();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Communities'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(48),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'all', label: Text('All')),
                ButtonSegment(value: 'club', label: Text('Clubs')),
                ButtonSegment(value: 'interest', label: Text('Interests')),
              ],
              selected: {_kind ?? 'all'},
              showSelectedIcon: false,
              onSelectionChanged: (value) {
                setState(() => _kind = value.first == 'all' ? null : value.first);
                _load();
              },
            ),
          ),
        ),
      ),
      floatingActionButton: canCreate
          ? FloatingActionButton.extended(
              onPressed: _create,
              icon: const Icon(Icons.add_rounded),
              label: const Text('Create community'),
            )
          : null,
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const SkeletonList()
            : _error != null && _groups.isEmpty
                ? ListView(children: [ErrorView(message: _error!, onRetry: _load)])
                : _groups.isEmpty
                    ? ListView(
                        children: const [
                          EmptyState(
                            icon: Icons.groups_outlined,
                            title: 'No groups yet',
                            message: 'Create the first community for your club, interest, or batch.',
                          ),
                        ],
                      )
                    : ListView(
                        padding: const EdgeInsets.only(bottom: 96),
                        children: [
                          if (mine.isNotEmpty) ...[
                            const SectionHeader(title: 'Your groups'),
                            ...mine.map(_tile),
                          ],
                          if (others.isNotEmpty) ...[
                            const SectionHeader(title: 'Discover'),
                            ...others.map(_tile),
                          ],
                        ],
                      ),
      ),
    );
  }

  Widget _tile(Group group) {
    final scheme = Theme.of(context).colorScheme;
    return ListTile(
      onTap: () async {
        await Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => GroupDetailScreen(slug: group.slug)),
        );
        _load();
      },
      leading: CircleAvatar(
        backgroundColor: scheme.primary.withValues(alpha: 0.12),
        child: Icon(
          group.kind == 'club' ? Icons.school_outlined : Icons.interests_outlined,
          color: scheme.primary,
          size: 20,
        ),
      ),
      title: Text(group.name, style: const TextStyle(fontWeight: FontWeight.w600)),
      subtitle: Text(
        '${group.memberCount} ${group.memberCount == 1 ? 'member' : 'members'}'
        '${group.description.isEmpty ? '' : ' · ${group.description}'}',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: group.joined
          ? Icon(Icons.check_circle_rounded, size: 20, color: scheme.secondary)
          : const Icon(Icons.chevron_right_rounded),
    );
  }
}

class GroupDetailScreen extends StatefulWidget {
  const GroupDetailScreen({super.key, required this.slug});

  final String slug;

  @override
  State<GroupDetailScreen> createState() => _GroupDetailScreenState();
}

class _GroupDetailScreenState extends State<GroupDetailScreen> {
  Group? _group;
  List<UserCard> _members = const [];
  List<Post> _posts = const [];
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final repo = context.read<Repository>();
      final (group, members) = await repo.groupDetail(widget.slug);
      final posts = await repo.feed(groupId: group.id);
      if (!mounted) return;
      setState(() {
        _group = group;
        _members = members;
        _posts = posts.items;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  Future<void> _toggle() async {
    try {
      await context.read<Repository>().toggleGroup(widget.slug);
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final canWrite = context.select<Session, bool>((s) => s.canWrite);
    final group = _group;

    return Scaffold(
      appBar: AppBar(title: Text(group?.name ?? 'Group')),
      floatingActionButton: group != null && group.joined && canWrite
          ? FloatingActionButton.extended(
              onPressed: () async {
                final created = await Navigator.of(context).push<bool>(
                  MaterialPageRoute(
                    builder: (_) => ComposerScreen(groupId: group.id, groupName: group.name),
                  ),
                );
                if (created == true) _load();
              },
              icon: const Icon(Icons.edit_outlined),
              label: const Text('Post'),
            )
          : null,
      body: group == null
          ? (_error != null
              ? ErrorView(message: _error!, onRetry: _load)
              : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.only(bottom: 96),
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(group.name, style: theme.textTheme.headlineSmall),
                        const SizedBox(height: 8),
                        Text(
                          '${group.memberCount} members · ${group.kind == 'club' ? 'Club' : 'Interest group'}',
                          style: theme.textTheme.bodySmall,
                        ),
                        if (group.description.isNotEmpty) ...[
                          const SizedBox(height: 12),
                          Text(group.description, style: theme.textTheme.bodyMedium),
                        ],
                        const SizedBox(height: 16),
                        if (canWrite)
                          group.joined
                              ? OutlinedButton.icon(
                                  onPressed: _toggle,
                                  icon: const Icon(Icons.logout_rounded, size: 18),
                                  label: const Text('Leave group'),
                                )
                              : FilledButton.icon(
                                  onPressed: _toggle,
                                  icon: const Icon(Icons.add_rounded),
                                  label: const Text('Join group'),
                                ),
                      ],
                    ),
                  ),
                  if (_members.isNotEmpty) ...[
                    const SectionHeader(title: 'Members'),
                    SizedBox(
                      height: 44,
                      child: ListView.separated(
                        scrollDirection: Axis.horizontal,
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        itemCount: _members.length,
                        separatorBuilder: (_, __) => const SizedBox(width: 10),
                        itemBuilder: (_, index) => InkWell(
                          onTap: _members[index].handle == null
                              ? null
                              : () => Navigator.of(context).push(
                                    MaterialPageRoute(
                                      builder: (_) => ProfileScreen(handle: _members[index].handle),
                                    ),
                                  ),
                          child: Avatar(user: _members[index], radius: 20),
                        ),
                      ),
                    ),
                  ],
                  const SectionHeader(title: 'Posts'),
                  if (_posts.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                      child: Text(
                        group.joined
                            ? 'Nothing posted here yet. Start the conversation.'
                            : 'Join the group to read and post here.',
                        style: theme.textTheme.bodyMedium?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ),
                  for (final post in _posts)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
                      child: PostCard(
                        post: post,
                        canVote: canWrite,
                        onTap: () async {
                          await Navigator.of(context).push(
                            MaterialPageRoute(builder: (_) => PostDetailScreen(postId: post.id)),
                          );
                          _load();
                        },
                      ),
                    ),
                ],
              ),
            ),
    );
  }
}
