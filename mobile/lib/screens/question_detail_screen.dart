import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import '../widgets/vote_bar.dart';
import 'profile_screen.dart';

class QuestionDetailScreen extends StatefulWidget {
  const QuestionDetailScreen({super.key, required this.questionId});

  final int questionId;

  @override
  State<QuestionDetailScreen> createState() => _QuestionDetailScreenState();
}

class _QuestionDetailScreenState extends State<QuestionDetailScreen> {
  final _answer = TextEditingController();
  final _answerFocus = FocusNode();

  Question? _question;
  List<Answer> _answers = const [];
  bool _canAccept = false;
  String? _error;
  bool _sending = false;
  bool _asModerator = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _answer.dispose();
    _answerFocus.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final (question, answers, canAccept) =
          await context.read<Repository>().questionDetail(widget.questionId);
      if (!mounted) return;
      setState(() {
        _question = question;
        _answers = answers;
        _canAccept = canAccept;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  Future<void> _submit() async {
    final body = _answer.text.trim();
    if (body.length < 10 || _sending) {
      if (body.length < 10) showMessage(context, 'An answer needs more than a few words.');
      return;
    }
    setState(() => _sending = true);
    try {
      await context.read<Repository>().answer(widget.questionId, body, asModerator: _asModerator);
      _answer.clear();
      _answerFocus.unfocus();
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _accept(Answer answer) async {
    try {
      await context.read<Repository>().acceptAnswer(widget.questionId, answer.id);
      await _load();
      if (mounted) showMessage(context, 'Marked as the answer.');
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final canWrite = context.select<Session, bool>((s) => s.canWrite);
    final isModerator = context.select<Session, bool>((s) => s.me?.card.isModerator ?? false);
    final question = _question;

    return Scaffold(
      appBar: AppBar(title: const Text('Question')),
      body: question == null
          ? (_error != null
              ? ErrorView(message: _error!, onRetry: _load)
              : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
                children: [
                  Text(question.title, style: theme.textTheme.headlineSmall?.copyWith(height: 1.25)),
                  const SizedBox(height: 14),
                  AuthorLine(
                    user: question.author,
                    timestamp: question.createdAt,
                    dense: true,
                    onTap: question.author.handle == null
                        ? null
                        : () => Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) => ProfileScreen(handle: question.author.handle),
                              ),
                            ),
                  ),
                  const SizedBox(height: 16),
                  Text(question.body, style: theme.textTheme.bodyLarge),
                  const SizedBox(height: 16),
                  if (question.tags.isNotEmpty || question.subject != null)
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        if (question.subject != null && question.subject!.isNotEmpty)
                          Chip(label: Text(question.subject!)),
                        ...question.tags.map((tag) => TagChip(tag: tag)),
                      ],
                    ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      VoteBar(
                        targetType: 'question',
                        targetId: question.id,
                        score: question.score,
                        myVote: question.myVote,
                        canVote: canWrite,
                        horizontal: true,
                      ),
                      const Spacer(),
                      Text(
                        '${question.viewCount} views',
                        style: theme.textTheme.bodySmall,
                      ),
                    ],
                  ),
                  const Divider(height: 32),
                  Text(
                    _answers.isEmpty
                        ? 'No answers yet'
                        : '${_answers.length} ${_answers.length == 1 ? 'answer' : 'answers'}',
                    style: theme.textTheme.titleSmall,
                  ),
                  const SizedBox(height: 12),
                  if (_answers.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                      child: Text(
                        canWrite
                            ? 'Be the one who answers it properly.'
                            : 'Verify your ID to answer questions.',
                        style: theme.textTheme.bodyMedium?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ),
                  for (final answer in _answers)
                    _AnswerTile(
                      answer: answer,
                      canVote: canWrite,
                      canAccept: _canAccept && !answer.isAccepted,
                      onAccept: () => _accept(answer),
                      onAuthorTap: answer.author.handle == null
                          ? null
                          : () => Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (_) => ProfileScreen(handle: answer.author.handle),
                                ),
                              ),
                    ),
                ],
              ),
            ),
      bottomNavigationBar: canWrite && question != null
          ? SafeArea(
              child: Container(
                decoration: BoxDecoration(
                  color: theme.colorScheme.surface,
                  border: Border(top: BorderSide(color: theme.colorScheme.outlineVariant)),
                ),
                padding: const EdgeInsets.fromLTRB(14, 8, 8, 8),
                child: Row(
                  children: [
                    if (isModerator)
                      IconButton(
                        tooltip: _asModerator ? 'Answer as @mod' : 'Answer as me',
                        onPressed: _sending ? null : () => setState(() => _asModerator = !_asModerator),
                        icon: Icon(_asModerator ? Icons.shield_rounded : Icons.person_outline_rounded),
                      ),
                    Expanded(
                      child: TextField(
                        controller: _answer,
                        focusNode: _answerFocus,
                        minLines: 1,
                        maxLines: 5,
                        textCapitalization: TextCapitalization.sentences,
                        decoration: const InputDecoration(
                          hintText: 'Write an answerÃ¢â‚¬Â¦',
                          contentPadding: EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        ),
                      ),
                    ),
                    IconButton.filled(
                      onPressed: _sending ? null : _submit,
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
              ),
            )
          : null,
    );
  }
}

class _AnswerTile extends StatelessWidget {
  const _AnswerTile({
    required this.answer,
    required this.canVote,
    required this.canAccept,
    required this.onAccept,
    this.onAuthorTap,
  });

  final Answer answer;
  final bool canVote;
  final bool canAccept;
  final VoidCallback onAccept;
  final VoidCallback? onAuthorTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.fromLTRB(14, 12, 10, 6),
      decoration: BoxDecoration(
        color: answer.isAccepted ? scheme.secondary.withValues(alpha: 0.07) : theme.cardTheme.color,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: answer.isAccepted
              ? scheme.secondary.withValues(alpha: 0.5)
              : scheme.outlineVariant.withValues(alpha: 0.6),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (answer.isAccepted) ...[
            Row(
              children: [
                Icon(Icons.check_circle_rounded, size: 16, color: scheme.secondary),
                const SizedBox(width: 6),
                Text(
                  'Accepted answer',
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: scheme.secondary,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
          ],
          AuthorLine(
            user: answer.author,
            timestamp: answer.createdAt,
            dense: true,
            onTap: onAuthorTap,
          ),
          const SizedBox(height: 10),
          Text(answer.body, style: theme.textTheme.bodyMedium),
          Row(
            children: [
              VoteBar(
                targetType: 'answer',
                targetId: answer.id,
                score: answer.score,
                myVote: answer.myVote,
                canVote: canVote,
                horizontal: true,
              ),
              const Spacer(),
              if (canAccept)
                TextButton.icon(
                  onPressed: onAccept,
                  icon: const Icon(Icons.check_rounded, size: 17),
                  label: const Text('Accept'),
                )
              else
                Padding(
                  padding: const EdgeInsets.only(right: 10),
                  child: Text(
                    relativeTime(answer.createdAt),
                    style: theme.textTheme.bodySmall?.copyWith(fontSize: 11.5),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}
