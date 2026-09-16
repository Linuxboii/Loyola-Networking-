import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import 'question_detail_screen.dart';

/// Ask a question.
///
/// Duplicate hints appear as you type the title. Showing them *before* posting
/// is what keeps a Q&A archive useful in its second year instead of sprawling
/// into forty copies of "when do results come out".
class AskScreen extends StatefulWidget {
  const AskScreen({super.key});

  @override
  State<AskScreen> createState() => _AskScreenState();
}

class _AskScreenState extends State<AskScreen> {
  final _title = TextEditingController();
  final _body = TextEditingController();
  final _tags = TextEditingController();
  final _subject = TextEditingController();

  Timer? _debounce;
  List<Question> _duplicates = const [];
  bool _anonymous = false;
  bool _sending = false;

  @override
  void dispose() {
    _debounce?.cancel();
    _title.dispose();
    _body.dispose();
    _tags.dispose();
    _subject.dispose();
    super.dispose();
  }

  void _onTitleChanged(String value) {
    setState(() {});
    _debounce?.cancel();
    if (value.trim().length < 8) {
      setState(() => _duplicates = const []);
      return;
    }
    _debounce = Timer(const Duration(milliseconds: 500), () async {
      try {
        final hits = await context.read<Repository>().duplicateHints(value.trim());
        if (mounted) setState(() => _duplicates = hits);
      } catch (_) {
        // Hints are advisory; silence is better than an error toast mid-typing.
      }
    });
  }

  bool get _valid => _title.text.trim().length >= 10 && _body.text.trim().length >= 15;

  Future<void> _send() async {
    if (!_valid || _sending) return;
    setState(() => _sending = true);
    try {
      final question = await context.read<Repository>().ask(
            title: _title.text.trim(),
            body: _body.text.trim(),
            tags: _tags.text
                .replaceAll(',', ' ')
                .split(RegExp(r'\s+'))
                .where((t) => t.trim().isNotEmpty)
                .toList(),
            subject: _subject.text.trim().isEmpty ? null : _subject.text.trim(),
            anonymous: _anonymous,
          );
      if (!mounted) return;
      Navigator.of(context).pop(true);
      Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => QuestionDetailScreen(questionId: question.id)),
      );
    } catch (error) {
      if (mounted) {
        showError(context, error);
        setState(() => _sending = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Ask a question'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: FilledButton(
              onPressed: _valid && !_sending ? _send : null,
              style: FilledButton.styleFrom(minimumSize: const Size(72, 38)),
              child: _sending
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                    )
                  : const Text('Ask'),
            ),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 40),
        children: [
          TextField(
            controller: _title,
            autofocus: true,
            textCapitalization: TextCapitalization.sentences,
            maxLength: 250,
            decoration: const InputDecoration(
              labelText: 'Question',
              hintText: 'How is the internal assessment split for third semester?',
              helperText: 'Ask it the way you would say it out loud.',
            ),
            onChanged: _onTitleChanged,
          ),
          if (_duplicates.isNotEmpty) ...[
            const SizedBox(height: 4),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: theme.colorScheme.secondary.withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Already asked?',
                    style: theme.textTheme.titleSmall?.copyWith(color: theme.colorScheme.secondary),
                  ),
                  const SizedBox(height: 6),
                  for (final duplicate in _duplicates.take(3))
                    InkWell(
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (_) => QuestionDetailScreen(questionId: duplicate.id),
                        ),
                      ),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(vertical: 6),
                        child: Row(
                          children: [
                            Expanded(
                              child: Text(
                                duplicate.title,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: theme.textTheme.bodySmall,
                              ),
                            ),
                            const SizedBox(width: 8),
                            Text(
                              '${duplicate.answerCount} ans',
                              style: theme.textTheme.bodySmall?.copyWith(fontSize: 11.5),
                            ),
                          ],
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 14),
          TextField(
            controller: _body,
            minLines: 5,
            maxLines: null,
            textCapitalization: TextCapitalization.sentences,
            decoration: const InputDecoration(
              labelText: 'Details',
              hintText: 'What have you already tried or checked? Which course and semester?',
              alignLabelWithHint: true,
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: _subject,
            decoration: const InputDecoration(
              labelText: 'Subject (optional)',
              hintText: 'Data Structures',
              prefixIcon: Icon(Icons.menu_book_outlined),
            ),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: _tags,
            decoration: const InputDecoration(
              labelText: 'Tags',
              hintText: 'exams semester3 internals',
              prefixIcon: Icon(Icons.tag_rounded),
            ),
          ),
          const SizedBox(height: 10),
          SwitchListTile(
            value: _anonymous,
            onChanged: (value) => setState(() => _anonymous = value),
            contentPadding: EdgeInsets.zero,
            title: const Text('Ask anonymously'),
            subtitle: Text(
              'Your name is hidden from readers. Moderators can still trace abuse, '
              'so the doubt box stays safe rather than lawless.',
              style: theme.textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}
