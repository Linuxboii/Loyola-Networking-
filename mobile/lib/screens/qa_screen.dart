import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../core/session.dart';
import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import 'ask_screen.dart';
import 'question_detail_screen.dart';

/// Academic Q&A — the half of the network the college cares about most.
class QaScreen extends StatefulWidget {
  const QaScreen({super.key});

  @override
  State<QaScreen> createState() => _QaScreenState();
}

class _QaScreenState extends State<QaScreen> {
  final _search = TextEditingController();
  final List<Question> _questions = [];

  String _scope = 'all';
  bool _initialLoad = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final page = await context.read<Repository>().questions(
            q: _search.text.trim(),
            scope: _scope,
          );
      if (!mounted) return;
      setState(() {
        _questions
          ..clear()
          ..addAll(page.items);
        _initialLoad = false;
      });
    } catch (error) {
      if (mounted) {
        setState(() {
          _error = '$error';
          _initialLoad = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final canWrite = context.select<Session, bool>((s) => s.canWrite);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Questions'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(104),
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
                child: TextField(
                  controller: _search,
                  textInputAction: TextInputAction.search,
                  onSubmitted: (_) => _load(),
                  decoration: InputDecoration(
                    hintText: 'Search questions',
                    prefixIcon: const Icon(Icons.search_rounded),
                    isDense: true,
                    suffixIcon: _search.text.isEmpty
                        ? null
                        : IconButton(
                            icon: const Icon(Icons.close_rounded, size: 18),
                            onPressed: () {
                              _search.clear();
                              _load();
                            },
                          ),
                  ),
                  onChanged: (_) => setState(() {}),
                ),
              ),
              SizedBox(
                height: 42,
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  children: [
                    for (final option in const [
                      ('all', 'All'),
                      ('unanswered', 'Unanswered'),
                      ('accepted', 'Answered'),
                      ('mine', 'Mine'),
                    ])
                      Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: ChoiceChip(
                          selected: _scope == option.$1,
                          label: Text(option.$2),
                          onSelected: (_) {
                            setState(() => _scope = option.$1);
                            _load();
                          },
                        ),
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
              onPressed: () async {
                final asked = await Navigator.of(context)
                    .push<bool>(MaterialPageRoute(builder: (_) => const AskScreen()));
                if (asked == true) _load();
              },
              icon: const Icon(Icons.help_outline_rounded),
              label: const Text('Ask'),
            )
          : null,
      body: RefreshIndicator(
        onRefresh: _load,
        child: _initialLoad
            ? const SkeletonList()
            : _error != null && _questions.isEmpty
                ? ListView(children: [ErrorView(message: _error!, onRetry: _load)])
                : _questions.isEmpty
                    ? ListView(
                        children: [
                          EmptyState(
                            icon: Icons.quiz_outlined,
                            title: 'No questions here',
                            message: _scope == 'unanswered'
                                ? 'Every question has an answer right now. That is a good day.'
                                : 'Ask the first one — someone in your batch has been through it.',
                          ),
                        ],
                      )
                    : ListView.separated(
                        padding: const EdgeInsets.fromLTRB(14, 12, 14, 96),
                        itemCount: _questions.length,
                        separatorBuilder: (_, __) => const SizedBox(height: 10),
                        itemBuilder: (context, index) => _QuestionTile(
                          question: _questions[index],
                          onTap: () async {
                            await Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) =>
                                    QuestionDetailScreen(questionId: _questions[index].id),
                              ),
                            );
                            _load();
                          },
                        ),
                      ),
      ),
    );
  }
}

class _QuestionTile extends StatelessWidget {
  const _QuestionTile({required this.question, required this.onTap});

  final Question question;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;

    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      question.title,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleMedium?.copyWith(height: 1.3),
                    ),
                  ),
                  if (question.isAnswered) ...[
                    const SizedBox(width: 10),
                    Icon(Icons.check_circle_rounded, size: 19, color: scheme.secondary),
                  ],
                ],
              ),
              const SizedBox(height: 8),
              Text(
                question.body,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Avatar(user: question.author, radius: 11),
                  const SizedBox(width: 7),
                  Expanded(
                    child: Text(
                      '${question.author.fullName} · ${relativeTime(question.createdAt)}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodySmall?.copyWith(fontSize: 12),
                    ),
                  ),
                  _Stat(icon: Icons.forum_outlined, value: question.answerCount),
                  const SizedBox(width: 12),
                  _Stat(icon: Icons.trending_up_rounded, value: question.score),
                ],
              ),
              if (question.tags.isNotEmpty || question.subject != null) ...[
                const SizedBox(height: 10),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    if (question.subject != null && question.subject!.isNotEmpty)
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                          color: scheme.primary.withValues(alpha: 0.10),
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Text(
                          question.subject!,
                          style: TextStyle(
                            fontSize: 11.5,
                            fontWeight: FontWeight.w700,
                            color: scheme.primary,
                          ),
                        ),
                      ),
                    ...question.tags.map((tag) => TagChip(tag: tag)),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.icon, required this.value});

  final IconData icon;
  final int value;

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.onSurfaceVariant;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 15, color: color),
        const SizedBox(width: 4),
        Text('$value', style: TextStyle(fontSize: 12.5, color: color, fontWeight: FontWeight.w600)),
      ],
    );
  }
}
