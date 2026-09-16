import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../data/repository.dart';
import 'common.dart';

/// Up/down control shared by posts, comments, questions and answers.
///
/// It updates optimistically and rolls back if the server disagrees — the
/// server is the authority on scores (it applies vote weighting and anti-gaming
/// rules), so the number that arrives back always wins.
class VoteBar extends StatefulWidget {
  const VoteBar({
    super.key,
    required this.targetType,
    required this.targetId,
    required this.score,
    required this.myVote,
    required this.canVote,
    this.onChanged,
    this.horizontal = false,
  });

  final String targetType;
  final int targetId;
  final int score;
  final int myVote;
  final bool canVote;
  final void Function(int score, int myVote)? onChanged;
  final bool horizontal;

  @override
  State<VoteBar> createState() => _VoteBarState();
}

class _VoteBarState extends State<VoteBar> {
  late int _score = widget.score;
  late int _myVote = widget.myVote;
  bool _sending = false;

  @override
  void didUpdateWidget(VoteBar old) {
    super.didUpdateWidget(old);
    if (old.score != widget.score || old.myVote != widget.myVote) {
      _score = widget.score;
      _myVote = widget.myVote;
    }
  }

  Future<void> _cast(int value) async {
    if (_sending) return;
    if (!widget.canVote) {
      showMessage(context, 'Verify your student ID to vote.');
      return;
    }

    final previousScore = _score;
    final previousVote = _myVote;
    // Tapping the active arrow clears the vote; the server toggles the same way.
    final nextVote = _myVote == value ? 0 : value;
    setState(() {
      _sending = true;
      _myVote = nextVote;
      _score = previousScore - previousVote + nextVote;
    });

    try {
      final result = await context.read<Repository>().vote(widget.targetType, widget.targetId, value);
      if (!mounted) return;
      setState(() {
        _score = result.score;
        _myVote = result.myVote;
      });
      widget.onChanged?.call(result.score, result.myVote);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _score = previousScore;
        _myVote = previousVote;
      });
      showError(context, error);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final up = _button(Icons.keyboard_arrow_up_rounded, 1, scheme.primary);
    final down = _button(Icons.keyboard_arrow_down_rounded, -1, scheme.error);
    final label = Text(
      compactCount(_score),
      style: TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w700,
        color: _myVote == 1
            ? scheme.primary
            : _myVote == -1
                ? scheme.error
                : scheme.onSurfaceVariant,
      ),
    );

    if (widget.horizontal) {
      return Row(mainAxisSize: MainAxisSize.min, children: [up, label, down]);
    }
    return Column(mainAxisSize: MainAxisSize.min, children: [up, label, down]);
  }

  Widget _button(IconData icon, int value, Color activeColor) {
    final active = _myVote == value;
    return IconButton(
      onPressed: () => _cast(value),
      icon: Icon(icon),
      iconSize: 24,
      visualDensity: VisualDensity.compact,
      padding: const EdgeInsets.all(4),
      constraints: const BoxConstraints(minWidth: 34, minHeight: 30),
      color: active ? activeColor : Theme.of(context).colorScheme.onSurfaceVariant,
      tooltip: value == 1 ? 'Helpful' : 'Not helpful',
    );
  }
}
