import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/config.dart';
import '../core/format.dart';
import '../core/theme.dart';
import '../models/models.dart';

/// Round avatar that falls back to initials — most students will not have a
/// photo until their ID card is approved, so the fallback is the common case.
class Avatar extends StatelessWidget {
  const Avatar({super.key, required this.user, this.radius = 20});

  final UserCard user;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final url = AppConfig.absolute(user.photoUrl);
    final background = user.anonymous
        ? scheme.surfaceContainerHighest
        : tierColor(user.repTier, scheme).withValues(alpha: 0.16);

    return CircleAvatar(
      radius: radius,
      backgroundColor: background,
      foregroundImage: url == null
          ? null
          : NetworkImage(url, headers: context.read<ApiClient>().imageHeaders),
      child: Text(
        user.anonymous ? '?' : user.initials,
        style: TextStyle(
          fontSize: radius * 0.8,
          fontWeight: FontWeight.w700,
          color: user.anonymous ? scheme.onSurfaceVariant : tierColor(user.repTier, scheme),
        ),
      ),
    );
  }
}

/// Small pill showing a member's reputation tier.
class TierBadge extends StatelessWidget {
  const TierBadge({super.key, required this.tier, this.compact = false});

  final String? tier;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    if (tier == null || tier!.isEmpty || tier == 'Newcomer') return const SizedBox.shrink();
    final color = tierColor(tier, Theme.of(context).colorScheme);
    return Container(
      padding: EdgeInsets.symmetric(horizontal: compact ? 6 : 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        tier!,
        style: TextStyle(fontSize: compact ? 10 : 11, fontWeight: FontWeight.w700, color: color),
      ),
    );
  }
}

/// Author line used by posts, comments, questions and answers.
class AuthorLine extends StatelessWidget {
  const AuthorLine({
    super.key,
    required this.user,
    this.timestamp,
    this.trailing,
    this.onTap,
    this.dense = false,
  });

  final UserCard user;
  final DateTime? timestamp;
  final Widget? trailing;
  final VoidCallback? onTap;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return InkWell(
      onTap: user.anonymous ? null : onTap,
      borderRadius: BorderRadius.circular(8),
      child: Row(
        children: [
          Avatar(user: user, radius: dense ? 14 : 19),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(
                        user.fullName,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w700),
                      ),
                    ),
                    if (user.isModerator) ...[
                      const SizedBox(width: 4),
                      Icon(Icons.verified_rounded, size: 14, color: theme.colorScheme.primary),
                    ],
                    const SizedBox(width: 6),
                    TierBadge(tier: user.repTier, compact: true),
                  ],
                ),
                Text(
                  [
                    if (!user.anonymous) user.subtitle,
                    if (timestamp != null) relativeTime(timestamp),
                  ].where((s) => s.isNotEmpty).join(' · '),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                    fontSize: 12,
                  ),
                ),
              ],
            ),
          ),
          if (trailing != null) trailing!,
        ],
      ),
    );
  }
}

class TagChip extends StatelessWidget {
  const TagChip({super.key, required this.tag, this.onTap, this.selected = false});

  final String tag;
  final VoidCallback? onTap;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(999),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: selected ? scheme.primary.withValues(alpha: 0.14) : scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(999),
          border: selected ? Border.all(color: scheme.primary.withValues(alpha: 0.5)) : null,
        ),
        child: Text(
          '#$tag',
          style: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w600,
            color: selected ? scheme.primary : scheme.onSurfaceVariant,
          ),
        ),
      ),
    );
  }
}

class EmptyState extends StatelessWidget {
  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.action,
  });

  final IconData icon;
  final String title;
  final String message;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 36, vertical: 48),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 44, color: theme.colorScheme.onSurfaceVariant.withValues(alpha: 0.6)),
            const SizedBox(height: 16),
            Text(title, textAlign: TextAlign.center, style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(
              message,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
            if (action != null) ...[const SizedBox(height: 20), action!],
          ],
        ),
      ),
    );
  }
}

class ErrorView extends StatelessWidget {
  const ErrorView({super.key, required this.message, this.onRetry});

  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) => EmptyState(
        icon: Icons.cloud_off_rounded,
        title: 'That did not load',
        message: message,
        action: onRetry == null
            ? null
            : OutlinedButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh_rounded, size: 18),
                label: const Text('Try again'),
              ),
      );
}

/// Grey blocks standing in for content while the first page loads. Cheaper on
/// the eye than a spinner, and it keeps the layout from jumping.
class SkeletonList extends StatelessWidget {
  const SkeletonList({super.key, this.count = 4});

  final int count;

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.06);
    return ListView.separated(
      padding: const EdgeInsets.all(16),
      itemCount: count,
      separatorBuilder: (_, __) => const SizedBox(height: 14),
      itemBuilder: (_, __) => Container(
        height: 132,
        decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(16)),
      ),
    );
  }
}

class SectionHeader extends StatelessWidget {
  const SectionHeader({super.key, required this.title, this.action, this.onAction});

  final String title;
  final String? action;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 8, 8),
      child: Row(
        children: [
          Expanded(
            child: Text(
              title.toUpperCase(),
              style: theme.textTheme.labelMedium?.copyWith(
                letterSpacing: 1.1,
                fontWeight: FontWeight.w700,
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          if (action != null)
            TextButton(onPressed: onAction, child: Text(action!)),
        ],
      ),
    );
  }
}

/// Uniform error reporting. Everything a person sees about a failure comes
/// through here, so the wording stays consistent.
void showError(BuildContext context, Object error) {
  final message = error is ApiException ? error.message : 'Something went wrong. Try again.';
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: Theme.of(context).colorScheme.errorContainer,
        showCloseIcon: true,
      ),
    );
}

void showMessage(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}
