import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../data/repository.dart';
import '../models/models.dart';
import '../widgets/common.dart';
import 'profile_screen.dart';
import 'question_detail_screen.dart';

/// One search box across people, questions, notes, projects and opportunities.
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final _controller = TextEditingController();
  Timer? _debounce;

  Map<String, dynamic>? _results;
  bool _searching = false;
  String? _error;

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    if (value.trim().length < 2) {
      setState(() => _results = null);
      return;
    }
    _debounce = Timer(const Duration(milliseconds: 350), _run);
  }

  Future<void> _run() async {
    final query = _controller.text.trim();
    if (query.length < 2) return;
    setState(() {
      _searching = true;
      _error = null;
    });
    try {
      final results = await context.read<Repository>().search(query);
      if (!mounted) return;
      setState(() => _results = results);
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final results = _results;

    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: _controller,
          autofocus: true,
          textInputAction: TextInputAction.search,
          onChanged: _onChanged,
          onSubmitted: (_) => _run(),
          decoration: const InputDecoration(
            hintText: 'Search people, questions, notes…',
            border: InputBorder.none,
            filled: false,
            contentPadding: EdgeInsets.zero,
          ),
        ),
        actions: [
          if (_searching)
            const Padding(
              padding: EdgeInsets.only(right: 20),
              child: Center(
                child: SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            )
          else if (_controller.text.isNotEmpty)
            IconButton(
              onPressed: () {
                _controller.clear();
                setState(() => _results = null);
              },
              icon: const Icon(Icons.close_rounded),
            ),
        ],
      ),
      body: _error != null
          ? ErrorView(message: _error!, onRetry: _run)
          : results == null
              ? const EmptyState(
                  icon: Icons.search_rounded,
                  title: 'Find anyone, or anything asked before',
                  message: 'Search by name, skill, subject or keyword. '
                      'Members who opted out of search will not appear.',
                )
              : ListView(
                  padding: const EdgeInsets.only(bottom: 32),
                  children: [
                    ..._section<UserCard>(
                      'People',
                      results['people'],
                      UserCard.fromJson,
                      (person) => ListTile(
                        leading: Avatar(user: person, radius: 20),
                        title: Text(person.fullName),
                        subtitle: Text(person.subtitle),
                        trailing: TierBadge(tier: person.repTier, compact: true),
                        onTap: person.handle == null
                            ? null
                            : () => Navigator.of(context).push(
                                  MaterialPageRoute(
                                    builder: (_) => ProfileScreen(handle: person.handle),
                                  ),
                                ),
                      ),
                    ),
                    ..._section<Question>(
                      'Questions',
                      results['questions'],
                      Question.fromJson,
                      (question) => ListTile(
                        title: Text(question.title, maxLines: 2, overflow: TextOverflow.ellipsis),
                        subtitle: Text(
                          '${question.answerCount} answers'
                          '${question.isAnswered ? ' · accepted' : ''}',
                        ),
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute(
                            builder: (_) => QuestionDetailScreen(questionId: question.id),
                          ),
                        ),
                      ),
                    ),
                    ..._rawSection(
                      'Notes & resources',
                      results['resources'],
                      (item) => ListTile(
                        leading: const Icon(Icons.description_outlined),
                        title: Text('${item['title']}'),
                        subtitle: Text([
                          if (item['subject'] != null) '${item['subject']}',
                          if (item['kind'] != null) '${item['kind']}',
                        ].join(' · ')),
                      ),
                    ),
                    ..._rawSection(
                      'Projects',
                      results['projects'],
                      (item) => ListTile(
                        leading: const Icon(Icons.rocket_launch_outlined),
                        title: Text('${item['title']}'),
                        subtitle: Text('${item['summary'] ?? ''}',
                            maxLines: 2, overflow: TextOverflow.ellipsis),
                      ),
                    ),
                    ..._rawSection(
                      'Opportunities',
                      results['opportunities'],
                      (item) => ListTile(
                        leading: const Icon(Icons.work_outline_rounded),
                        title: Text('${item['title']}'),
                        subtitle: Text([
                          if (item['company'] != null) '${item['company']}',
                          if (item['location'] != null) '${item['location']}',
                        ].join(' · ')),
                      ),
                    ),
                    if (_isEmpty(results))
                      const EmptyState(
                        icon: Icons.search_off_rounded,
                        title: 'Nothing matched',
                        message: 'Try a shorter query, or a different spelling.',
                      ),
                    const SizedBox(height: 20),
                    Center(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 32),
                        child: Text(
                          'Only verified members appear in search, and anyone can hide '
                          'themselves from it in Settings.',
                          textAlign: TextAlign.center,
                          style: theme.textTheme.bodySmall,
                        ),
                      ),
                    ),
                  ],
                ),
    );
  }

  bool _isEmpty(Map<String, dynamic> results) => const [
        'people',
        'questions',
        'resources',
        'projects',
        'opportunities',
      ].every((key) => ((results[key] as List?) ?? const []).isEmpty);

  List<Widget> _section<T>(
    String title,
    dynamic raw,
    T Function(Map<String, dynamic>) parse,
    Widget Function(T) build,
  ) {
    final items = ((raw as List?) ?? const [])
        .map((e) => parse((e as Map).cast<String, dynamic>()))
        .toList();
    if (items.isEmpty) return const [];
    return [SectionHeader(title: title), ...items.map(build)];
  }

  List<Widget> _rawSection(
    String title,
    dynamic raw,
    Widget Function(Map<String, dynamic>) build,
  ) {
    final items = ((raw as List?) ?? const []).cast<Map>().map((e) => e.cast<String, dynamic>());
    if (items.isEmpty) return const [];
    return [SectionHeader(title: title), ...items.map(build)];
  }
}
