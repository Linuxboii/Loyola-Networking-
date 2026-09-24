import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/config.dart';
import '../core/image_cache.dart';
import '../core/format.dart';
import '../models/models.dart';
import 'common.dart';
import 'vote_bar.dart';

/// One post in the feed.
///
/// The card is deliberately quiet: no engagement counters shouting for
/// attention, no infinite autoplay. It shows who said what, when, and the two
/// actions that matter — vote and reply.
class PostCard extends StatelessWidget {
  const PostCard({
    super.key,
    required this.post,
    required this.canVote,
    this.onTap,
    this.onAuthorTap,
    this.onTagTap,
    this.onVoted,
    this.onPollVote,
    this.showFullBody = false,
    this.trailing,
  });

  final Post post;
  final bool canVote;
  final VoidCallback? onTap;
  final VoidCallback? onAuthorTap;
  final void Function(String tag)? onTagTap;
  final void Function(int score, int myVote)? onVoted;
  final void Function(int optionId)? onPollVote;
  final bool showFullBody;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 8, 4),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AuthorLine(
                user: post.author,
                timestamp: post.createdAt,
                onTap: onAuthorTap,
                trailing: trailing ??
                    (post.isOfficial
                        ? Padding(
                            padding: const EdgeInsets.only(right: 8),
                            child: _OfficialTag(),
                          )
                        : null),
              ),
              const SizedBox(height: 10),
              if (post.title != null && post.title!.isNotEmpty) ...[
                Text(
                  post.title!,
                  style: theme.textTheme.titleMedium?.copyWith(height: 1.3),
                  maxLines: showFullBody ? null : 3,
                  overflow: showFullBody ? null : TextOverflow.ellipsis,
                ),
                const SizedBox(height: 6),
              ],
              if (post.body.isNotEmpty)
                Text(
                  post.body,
                  style: theme.textTheme.bodyMedium,
                  maxLines: showFullBody ? null : 6,
                  overflow: showFullBody ? null : TextOverflow.ellipsis,
                ),
              if (post.media.isNotEmpty) ...[
                const SizedBox(height: 12),
                _PostImage(url: post.media.first),
              ],
              if (post.linkUrl != null && post.linkUrl!.isNotEmpty) ...[
                const SizedBox(height: 10),
                _LinkPreview(url: post.linkUrl!),
              ],
              if (post.poll != null && post.poll!.isNotEmpty) ...[
                const SizedBox(height: 12),
                _Poll(
                  options: post.poll!,
                  myOption: post.myPollOption,
                  onVote: canVote ? onPollVote : null,
                ),
              ],
              if (post.tags.isNotEmpty) ...[
                const SizedBox(height: 12),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: post.tags
                      .map((tag) => TagChip(
                          tag: tag,
                          onTap:
                              onTagTap == null ? null : () => onTagTap!(tag)))
                      .toList(),
                ),
              ],
              const SizedBox(height: 4),
              Row(
                children: [
                  VoteBar(
                    targetType: 'post',
                    targetId: post.id,
                    score: post.score,
                    myVote: post.myVote,
                    canVote: canVote,
                    horizontal: true,
                    onChanged: onVoted,
                  ),
                  const SizedBox(width: 4),
                  TextButton.icon(
                    onPressed: onTap,
                    icon: const Icon(Icons.mode_comment_outlined, size: 17),
                    label: Text(compactCount(post.commentCount)),
                    style: TextButton.styleFrom(
                      foregroundColor: theme.colorScheme.onSurfaceVariant,
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                      minimumSize: const Size(0, 34),
                      textStyle: const TextStyle(
                          fontSize: 13, fontWeight: FontWeight.w600),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _OfficialTag extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: scheme.secondary.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        'NOTICE',
        style: TextStyle(
          fontSize: 10,
          letterSpacing: 0.8,
          fontWeight: FontWeight.w800,
          color: scheme.secondary,
        ),
      ),
    );
  }
}

class _PostImage extends StatelessWidget {
  const _PostImage({required this.url});

  final String url;

  @override
  Widget build(BuildContext context) {
    final absolute = AppConfig.absolute(url);
    if (absolute == null) return const SizedBox.shrink();
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: CampusCachedImage(
        url: absolute,
        httpHeaders: context.read<ApiClient>().imageHeaders,
        fit: BoxFit.cover,
        width: double.infinity,
      ),
    );
  }
}

class _LinkPreview extends StatelessWidget {
  const _LinkPreview({required this.url});

  final String url;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final host = Uri.tryParse(url)?.host ?? url;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          Icon(Icons.link_rounded, size: 18, color: scheme.onSurfaceVariant),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              host,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 13, color: scheme.onSurfaceVariant),
            ),
          ),
        ],
      ),
    );
  }
}

class _Poll extends StatelessWidget {
  const _Poll({required this.options, this.myOption, this.onVote});

  final List<PollOption> options;
  final int? myOption;
  final void Function(int optionId)? onVote;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final voted = myOption != null;
    final total = options.fold<int>(0, (sum, o) => sum + o.votes);

    return Column(
      children: [
        for (final option in options)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: InkWell(
              onTap: onVote == null ? null : () => onVote!(option.id),
              borderRadius: BorderRadius.circular(10),
              child: Stack(
                children: [
                  Container(
                    height: 42,
                    decoration: BoxDecoration(
                      color: scheme.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(10),
                      border: option.id == myOption
                          ? Border.all(color: scheme.primary, width: 1.4)
                          : null,
                    ),
                  ),
                  if (voted)
                    FractionallySizedBox(
                      widthFactor: (option.share / 100).clamp(0.0, 1.0),
                      child: Container(
                        height: 42,
                        decoration: BoxDecoration(
                          color: scheme.primary.withValues(alpha: 0.16),
                          borderRadius: BorderRadius.circular(10),
                        ),
                      ),
                    ),
                  SizedBox(
                    height: 42,
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              option.label,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontSize: 14,
                                fontWeight: option.id == myOption
                                    ? FontWeight.w700
                                    : FontWeight.w500,
                              ),
                            ),
                          ),
                          if (voted)
                            Text(
                              '${option.share}%',
                              style: TextStyle(
                                fontSize: 13,
                                fontWeight: FontWeight.w700,
                                color: scheme.onSurfaceVariant,
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        Align(
          alignment: Alignment.centerLeft,
          child: Text(
            voted
                ? '$total ${total == 1 ? 'vote' : 'votes'}'
                : 'Pick one to see the results',
            style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant),
          ),
        ),
      ],
    );
  }
}
