import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:loyola_networking/core/format.dart';
import 'package:loyola_networking/core/updater.dart';
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

  group('updates', () {
    ReleaseInfo release({int build = 5, bool mandatory = false, int floor = 1}) =>
        ReleaseInfo.fromJson({
          'build': build,
          'version': '1.2.0',
          'notes': 'Faster feed.',
          'size': 41943040,
          'sha256': 'abc123',
          'download_url': '/api/v1/updates/android/download/$build/app.apk',
          'mandatory': mandatory,
          'min_supported_build': floor,
        })!;

    UpdateStatus status({int current = 4, ReleaseInfo? latest, int floor = 1}) => UpdateStatus(
          currentBuild: current,
          currentVersion: '1.1.0',
          latest: latest,
          minSupportedBuild: floor,
        );

    test('a release without a build or a URL is not trusted', () {
      expect(ReleaseInfo.fromJson(null), isNull);
      expect(ReleaseInfo.fromJson({'version': '1.2.0'}), isNull);
      expect(ReleaseInfo.fromJson({'build': 5, 'download_url': ''}), isNull);
    });

    test('a newer build is offered, the same build is not', () {
      expect(status(current: 4, latest: release()).updateAvailable, isTrue);
      expect(status(current: 5, latest: release()).updateAvailable, isFalse);
      expect(status(current: 6, latest: release()).updateAvailable, isFalse);
      expect(status(latest: null).updateAvailable, isFalse);
    });

    test('an ordinary update can be dismissed', () {
      expect(status(current: 4, latest: release()).blocking, isFalse);
    });

    test('a mandatory release blocks', () {
      expect(status(current: 4, latest: release(mandatory: true)).blocking, isTrue);
    });

    test('a retired build blocks even with nothing newer published', () {
      final retired = status(current: 4, latest: null, floor: 5);
      expect(retired.unsupported, isTrue);
      expect(retired.blocking, isTrue);
    });

    test('size reads as megabytes, and vanishes when unknown', () {
      expect(release().sizeLabel, '40.0 MB');
      expect(
        ReleaseInfo.fromJson({'build': 5, 'download_url': '/a.apk', 'size': 0})!.sizeLabel,
        isEmpty,
      );
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
