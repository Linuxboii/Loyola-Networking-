import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import '../widgets/post_card.dart';
import 'post_detail_screen.dart';
import 'reputation_screen.dart';
import 'settings_screen.dart';
import 'admin_screen.dart';
import 'followers_screen.dart';

/// A member's profile. With no handle it shows the signed-in student's own.
class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, this.handle});

  final String? handle;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  Profile? _profile;
  List<Post> _posts = const [];
  String? _error;
  Map<String, dynamic> _community = const {};

  bool get _isOwnTab => widget.handle == null;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final handle = widget.handle ?? context.read<Session>().me?.card.handle;
    if (handle == null) return;
    setState(() => _error = null);
    try {
      final repo = context.read<Repository>();
      final profile = await repo.profile(handle);
      final community = await repo.communityProfile(handle);
      final posts = profile.activityHidden
          ? const <Post>[]
          : (await repo.profilePosts(handle)).items;
      if (!mounted) return;
      setState(() {
        _profile = profile;
        _community = community;
        _posts = posts;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  Future<void> _toggleFollow() async {
    final handle = _profile?.card.handle;
    if (handle == null) return;
    try {
      await context.read<Repository>().toggleFollow(handle);
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _editBio() async {
    final profile = _profile;
    if (profile == null) return;
    final bio = TextEditingController(text: profile.bio);
    final interests = TextEditingController(text: profile.interests.join(' '));

    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Edit profile'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: bio,
                minLines: 3,
                maxLines: 5,
                maxLength: 1000,
                textCapitalization: TextCapitalization.sentences,
                decoration: const InputDecoration(
                  labelText: 'About you',
                  alignLabelWithHint: true,
                ),
              ),
              TextField(
                controller: interests,
                decoration: const InputDecoration(
                  labelText: 'Interests',
                  helperText: 'Space separated. They shape your For You feed.',
                  prefixIcon: Icon(Icons.tag_rounded),
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Save')),
        ],
      ),
    );

    if (saved != true || !mounted) return;
    try {
      await context.read<Repository>().updateProfile({
        'bio': bio.text.trim(),
        'interests': interests.text
            .replaceAll(',', ' ')
            .split(RegExp(r'\s+'))
            .where((t) => t.trim().isNotEmpty)
            .toList(),
      });
      await _load();
      if (mounted) await context.read<Session>().refreshMe();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _addSkill() async {
    final controller = TextEditingController();
    final name = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Add a skill'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(
              hintText: 'Flutter, Public speaking, Circuit design'),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('Add'),
          ),
        ],
      ),
    );
    if (name == null || name.length < 2 || !mounted) return;
    try {
      await context.read<Repository>().addSkill(name);
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _removeSkill(int skillId) async {
    try {
      await context.read<Repository>().removeSkill(skillId);
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final session = context.watch<Session>();
    final profile = _profile;
    final canWrite = session.canWrite;

    return Scaffold(
      appBar: AppBar(
        title: Text(_isOwnTab ? 'You' : '@${profile?.card.handle ?? ''}'),
        actions: [
          if (_isOwnTab)
            IconButton(
              tooltip: 'Settings',
              onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => const SettingsScreen())),
              icon: const Icon(Icons.settings_outlined),
            ),
          if (session.me?.isAdmin ?? false)
            IconButton(
              tooltip: 'Community administration',
              onPressed: () => Navigator.of(context)
                  .push(MaterialPageRoute(builder: (_) => const AdminScreen())),
              icon: const Icon(Icons.admin_panel_settings_outlined),
            ),
        ],
      ),
      body: profile == null
          ? (_error != null
              ? ErrorView(message: _error!, onRetry: _load)
              : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.only(bottom: 40),
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Avatar(user: profile.card, radius: 34),
                            const SizedBox(width: 16),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    '@${profile.card.handle ?? ''}',
                                    style: theme.textTheme.titleLarge,
                                  ),
                                  const SizedBox(height: 8),
                                  Wrap(
                                    spacing: 6,
                                    runSpacing: 6,
                                    children: [
                                      TierBadge(tier: profile.card.repTier),
                                      if (_community['verified'] == true)
                                        const Chip(
                                            label: Text('Verified'),
                                            visualDensity:
                                                VisualDensity.compact),
                                      if (_community['is_og'] == true)
                                        const Chip(
                                            label: Text('OG'),
                                            visualDensity:
                                                VisualDensity.compact),
                                      if (profile.card.course != null &&
                                          profile.card.course!.isNotEmpty)
                                        Chip(
                                          label: Text(profile.card.course!),
                                          visualDensity: VisualDensity.compact,
                                        ),
                                      if (profile.displayYear.isNotEmpty)
                                        Chip(
                                          label: Text(profile.displayYear),
                                          visualDensity: VisualDensity.compact,
                                        ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                        if (profile.bio.isNotEmpty) ...[
                          const SizedBox(height: 16),
                          Text(profile.bio, style: theme.textTheme.bodyMedium),
                        ],
                        if (!profile.isMe) ...[
                          const SizedBox(height: 14),
                          FilledButton.icon(
                            onPressed: _toggleFollow,
                            icon: Icon(_community['following'] == true
                                ? Icons.person_remove_outlined
                                : Icons.person_add_alt_1_outlined),
                            label: Text(switch (_community['follow_state']) {
                              'accepted' => 'Following',
                              'pending' => 'Requested',
                              _ => _community['follows_you'] == true
                                  ? 'Follow back'
                                  : 'Follow'
                            }),
                          ),
                        ],
                        if (profile.isMe) ...[
                          const SizedBox(height: 14),
                          Row(
                            children: [
                              Expanded(
                                child: OutlinedButton.icon(
                                  onPressed: _editBio,
                                  icon:
                                      const Icon(Icons.edit_outlined, size: 17),
                                  label: const Text('Edit profile'),
                                ),
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: OutlinedButton.icon(
                                  onPressed: () => Navigator.of(context).push(
                                    MaterialPageRoute(
                                        builder: (_) =>
                                            const ReputationScreen()),
                                  ),
                                  icon: const Icon(Icons.insights_rounded,
                                      size: 17),
                                  label: const Text('Standing'),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 10),
                          OutlinedButton.icon(
                            onPressed: () => Navigator.of(context).push(
                                MaterialPageRoute(
                                    builder: (_) => const FollowersScreen())),
                            icon: const Icon(Icons.people_outline),
                            label: const Text(
                                'Followers · Following · Follow requests'),
                          ),
                        ],
                        const SizedBox(height: 18),
                        _StatRow(
                            counts: profile.counts,
                            repTotal: profile.card.repTotal),
                      ],
                    ),
                  ),
                  const SizedBox(height: 8),
                  _PillarBars(pillars: profile.repPillars),
                  SectionHeader(
                    title: 'Skills',
                    action: profile.isMe ? 'Add' : null,
                    onAction: profile.isMe ? _addSkill : null,
                  ),
                  if (profile.skills.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Text(
                        profile.isMe
                            ? 'Add what you are good at. Other students endorse it, and endorsements '
                                'from people who have actually worked with you carry more weight.'
                            : 'No skills listed yet.',
                        style: theme.textTheme.bodySmall,
                      ),
                    )
                  else
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: profile.skills
                            .map(
                              (skill) => Chip(
                                label: Text(
                                  skill.endorsements > 0
                                      ? '${skill.name} · ${skill.endorsements}'
                                      : skill.name,
                                ),
                                onDeleted: profile.isMe
                                    ? () => _removeSkill(skill.id)
                                    : null,
                              ),
                            )
                            .toList(),
                      ),
                    ),
                  const SectionHeader(title: 'Recent posts'),
                  if (profile.activityHidden)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Text(
                        'This member keeps their activity private.',
                        style: theme.textTheme.bodySmall,
                      ),
                    )
                  else if (_posts.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Text('Nothing posted yet.',
                          style: theme.textTheme.bodySmall),
                    )
                  else
                    for (final post in _posts)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
                        child: PostCard(
                          post: post,
                          canVote: canWrite,
                          onTap: () async {
                            await Navigator.of(context).push(
                              MaterialPageRoute(
                                  builder: (_) =>
                                      PostDetailScreen(postId: post.id)),
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

class _StatRow extends StatelessWidget {
  const _StatRow({required this.counts, required this.repTotal});

  final Map<String, int> counts;
  final double repTotal;

  @override
  Widget build(BuildContext context) {
    final entries = [
      ('Standing', repTotal.round().toString()),
      ('Posts', '${counts['posts'] ?? 0}'),
      ('Answers', '${counts['answers'] ?? 0}'),
      ('Accepted', '${counts['accepted_answers'] ?? 0}'),
    ];
    final theme = Theme.of(context);

    return Container(
      padding: const EdgeInsets.symmetric(vertical: 14),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          for (final entry in entries)
            Expanded(
              child: Column(
                children: [
                  Text(
                    entry.$2,
                    style: theme.textTheme.titleMedium
                        ?.copyWith(fontWeight: FontWeight.w800),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    entry.$1,
                    style: theme.textTheme.bodySmall?.copyWith(fontSize: 11.5),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _PillarBars extends StatelessWidget {
  const _PillarBars({required this.pillars});

  final Map<String, double> pillars;

  static const _labels = {
    'academic': 'Academic help',
    'build': 'Build & ship',
    'service': 'Community service',
    'participation': 'Participation',
    'conduct': 'Conduct',
  };

  @override
  Widget build(BuildContext context) {
    if (pillars.isEmpty) return const SizedBox.shrink();
    final theme = Theme.of(context);
    final max = pillars.values.fold<double>(1, (m, v) => v > m ? v : m);

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final entry in _labels.entries)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 5),
              child: Row(
                children: [
                  SizedBox(
                    width: 124,
                    child: Text(entry.value, style: theme.textTheme.bodySmall),
                  ),
                  Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(999),
                      child: LinearProgressIndicator(
                        value:
                            ((pillars[entry.key] ?? 0) / max).clamp(0.0, 1.0),
                        minHeight: 7,
                        backgroundColor:
                            theme.colorScheme.surfaceContainerHighest,
                        color: theme.colorScheme.primary,
                      ),
                    ),
                  ),
                  SizedBox(
                    width: 42,
                    child: Text(
                      compactCount((pillars[entry.key] ?? 0).round()),
                      textAlign: TextAlign.right,
                      style: theme.textTheme.bodySmall
                          ?.copyWith(fontWeight: FontWeight.w700),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
