import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:loyola_networking/core/format.dart';
import 'package:loyola_networking/models/models.dart';
import 'package:loyola_networking/widgets/common.dart';

void main() {
  group('formatting', () {
    test('relative time collapses to the coarsest useful unit', () {
      final now = DateTime.now();
      expect(relativeTime(now.subtract(const Duration(seconds: 5))), 'now');
      expect(relativeTime(now.subtract(const Duration(minutes: 7))), '7m');
      expect(relativeTime(now.subtract(const Duration(hours: 5))), '5h');
      expect(relativeTime(now.subtract(const Duration(days: 3))), '3d');
      expect(relativeTime(null), '');
    });

    test('counts compact once they would break a row', () {
      expect(compactCount(42), '42');
      expect(compactCount(1200), '1.2k');
      expect(compactCount(12000), '12k');
    });

    test('initials survive single and multi-word names', () {
      expect(initialsOf('Asha'), 'A');
      expect(initialsOf('Asha Menon'), 'AM');
      expect(initialsOf('  '), '?');
    });
  });

  group('models', () {
    test('a post parses with only the fields the server guarantees', () {
      final post = Post.fromJson({
        'id': 1,
        'kind': 'text',
        'body': 'Hello campus',
        'author': {'id': 2, 'full_name': 'Asha Menon', 'handle': 'asha'},
      });

      expect(post.id, 1);
      expect(post.tags, isEmpty);
      expect(post.score, 0);
      expect(post.author.handle, 'asha');
    });

    test('an anonymous author is labelled and carries no handle', () {
      final question = Question.fromJson({
        'id': 9,
        'title': 'Can I ask this anonymously?',
        'body': 'Testing the doubt box.',
        'is_anonymous': true,
        'author': {'id': null, 'full_name': 'Anonymous', 'anonymous': true},
      });

      expect(question.isAnonymous, isTrue);
      expect(question.author.anonymous, isTrue);
      expect(question.author.handle, isNull);
    });

    test('tier gates read and write separately', () {
      Me at(int tier) => Me.fromJson({
            'id': 1,
            'full_name': 'Test',
            'handle': 'test',
            'tier': tier,
            'tier_label': 'x',
            'status': 'active',
          });

      expect(at(0).canRead, isFalse);
      expect(at(1).canRead, isTrue);
      expect(at(1).canWrite, isFalse);
      expect(at(2).canWrite, isTrue);
    });
  });

  testWidgets('a tier badge stays out of the way for newcomers', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: TierBadge(tier: 'Newcomer'))),
    );
    expect(find.text('Newcomer'), findsNothing);

    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: TierBadge(tier: 'Trusted'))),
    );
    expect(find.text('Trusted'), findsOneWidget);
  });
}
