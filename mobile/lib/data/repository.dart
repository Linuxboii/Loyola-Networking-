import '../core/api_client.dart';
import '../models/models.dart';

class Page<T> {
  Page({required this.items, required this.hasMore, required this.page});

  final List<T> items;
  final bool hasMore;
  final int page;
}

class VoteResult {
  VoteResult({required this.score, required this.myVote});

  final int score;
  final int myVote;

  factory VoteResult.fromJson(Map<String, dynamic> json) => VoteResult(
        score: (json['score'] as num?)?.toInt() ?? 0,
        myVote: (json['my_vote'] as num?)?.toInt() ?? 0,
      );
}

/// Everything the screens are allowed to ask the server for.
///
/// One class, thin methods, no caching: state that needs to survive a rebuild
/// belongs to the screen that owns it, and the API is fast enough that a pull
/// to refresh is honest rather than decorative.
class Repository {
  Repository(this.api);

  final ApiClient api;

  // --- feed ---------------------------------------------------------------

  Future<Page<Post>> feed({
    String mode = 'latest',
    String? tag,
    String? kind,
    int? groupId,
    int page = 1,
  }) async {
    final data = await api.get('/feed', query: {
      'mode': mode,
      'tag': tag,
      'kind': kind,
      'group_id': groupId,
      'page': page,
    }) as Map<String, dynamic>;
    return Page(
      items: (data['posts'] as List).map((e) => Post.fromJson((e as Map).cast<String, dynamic>())).toList(),
      hasMore: data['has_more'] == true,
      page: (data['page'] as num?)?.toInt() ?? page,
    );
  }

  Future<Post> createPost({
    required String kind,
    String? title,
    required String body,
    List<String> tags = const [],
    String? linkUrl,
    int? groupId,
    List<String> media = const [],
    List<String> pollOptions = const [],
  }) async {
    final data = await api.post('/posts', body: {
      'kind': kind,
      'title': title,
      'body': body,
      'tags': tags,
      'link_url': linkUrl,
      'group_id': groupId,
      'media': media,
      'poll_options': pollOptions,
    }) as Map<String, dynamic>;
    return Post.fromJson(data);
  }

  Future<(Post, List<Comment>)> postDetail(int id) async {
    final data = await api.get('/posts/$id') as Map<String, dynamic>;
    final post = Post.fromJson((data['post'] as Map).cast<String, dynamic>());
    final comments = (data['comments'] as List)
        .map((e) => Comment.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
    return (post, comments);
  }

  Future<Comment> comment(int postId, String body, {int? parentId}) async {
    final data = await api.post(
      '/posts/$postId/comments',
      body: {'body': body, 'parent_id': parentId},
    ) as Map<String, dynamic>;
    return Comment.fromJson(data);
  }

  Future<VoteResult> vote(String targetType, int targetId, int value) async {
    final data = await api.post('/vote', body: {
      'target_type': targetType,
      'target_id': targetId,
      'value': value,
    }) as Map<String, dynamic>;
    return VoteResult.fromJson(data);
  }

  Future<(List<PollOption>, int?)> votePoll(int postId, int optionId) async {
    final data = await api.post('/posts/$postId/poll/$optionId') as Map<String, dynamic>;
    final options = ((data['poll'] as List?) ?? const [])
        .map((e) => PollOption.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
    return (options, data['my_poll_option'] as int?);
  }

  Future<void> deletePost(int id) => api.delete('/posts/$id');

  Future<void> deleteComment(int id) => api.delete('/comments/$id');

  Future<String> uploadImage(String filePath) async {
    final data = await api.upload('/media', field: 'file', filePath: filePath)
        as Map<String, dynamic>;
    return data['path'] as String;
  }

  Future<void> report(String targetType, int targetId, String reason, String detail) =>
      api.post('/reports', body: {
        'target_type': targetType,
        'target_id': targetId,
        'reason': reason,
        'detail': detail,
      });

  // --- q&a ----------------------------------------------------------------

  Future<Page<Question>> questions({
    String q = '',
    String scope = 'all',
    String? subject,
    int page = 1,
  }) async {
    final data = await api.get('/qa/questions', query: {
      'q': q,
      'scope': scope,
      'subject': subject,
      'page': page,
    }) as Map<String, dynamic>;
    return Page(
      items: (data['questions'] as List)
          .map((e) => Question.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
      hasMore: data['has_more'] == true,
      page: (data['page'] as num?)?.toInt() ?? page,
    );
  }

  Future<(Question, List<Answer>, bool)> questionDetail(int id) async {
    final data = await api.get('/qa/questions/$id') as Map<String, dynamic>;
    return (
      Question.fromJson((data['question'] as Map).cast<String, dynamic>()),
      (data['answers'] as List).map((e) => Answer.fromJson((e as Map).cast<String, dynamic>())).toList(),
      data['can_accept'] == true,
    );
  }

  Future<List<Question>> duplicateHints(String title) async {
    final data = await api.get('/qa/duplicates', query: {'title': title}) as Map<String, dynamic>;
    return (data['questions'] as List)
        .map((e) => Question.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
  }

  Future<Question> ask({
    required String title,
    required String body,
    List<String> tags = const [],
    String? subject,
    String? semester,
    bool anonymous = false,
  }) async {
    final data = await api.post('/qa/questions', body: {
      'title': title,
      'body': body,
      'tags': tags,
      'subject': subject,
      'semester': semester,
      'is_anonymous': anonymous,
    }) as Map<String, dynamic>;
    return Question.fromJson(data);
  }

  Future<Answer> answer(int questionId, String body) async {
    final data = await api.post('/qa/questions/$questionId/answers', body: {'body': body})
        as Map<String, dynamic>;
    return Answer.fromJson(data);
  }

  Future<void> acceptAnswer(int questionId, int answerId) =>
      api.post('/qa/questions/$questionId/accept/$answerId');

  // --- groups -------------------------------------------------------------

  Future<List<Group>> groups({String? kind}) async {
    final data = await api.get('/groups', query: {'kind': kind}) as Map<String, dynamic>;
    return (data['groups'] as List)
        .map((e) => Group.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
  }

  Future<(Group, List<UserCard>)> groupDetail(String slug) async {
    final data = await api.get('/groups/$slug') as Map<String, dynamic>;
    return (
      Group.fromJson(data),
      (data['members'] as List).map((e) => UserCard.fromJson((e as Map).cast<String, dynamic>())).toList(),
    );
  }

  Future<bool> toggleGroup(String slug) async {
    final data = await api.post('/groups/$slug/join') as Map<String, dynamic>;
    return data['joined'] == true;
  }

  Future<Group> createGroup(String name, String description, String kind) async {
    final data = await api.post('/groups', body: {
      'name': name,
      'description': description,
      'kind': kind,
    }) as Map<String, dynamic>;
    return Group.fromJson(data);
  }

  // --- events -------------------------------------------------------------

  Future<List<CampusEvent>> events({String when = 'upcoming'}) async {
    final data = await api.get('/events', query: {'when': when}) as Map<String, dynamic>;
    return (data['events'] as List)
        .map((e) => CampusEvent.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
  }

  Future<(CampusEvent, List<UserCard>, bool)> eventDetail(int id) async {
    final data = await api.get('/events/$id') as Map<String, dynamic>;
    return (
      CampusEvent.fromJson(data),
      (data['attendees'] as List).map((e) => UserCard.fromJson((e as Map).cast<String, dynamic>())).toList(),
      data['is_host'] == true,
    );
  }

  Future<void> rsvp(int eventId, {String state = 'going', String role = 'attendee'}) =>
      api.post('/events/$eventId/rsvp', body: {'state': state}, query: {'role': role});

  Future<Map<String, dynamic>> checkin(int eventId, String code) async =>
      (await api.post('/events/$eventId/checkin', query: {'code': code})) as Map<String, dynamic>;

  Future<CampusEvent> createEvent({
    required String title,
    required String description,
    required String venue,
    required DateTime startsAt,
    DateTime? endsAt,
    int? capacity,
    List<String> tags = const [],
    int? groupId,
  }) async {
    final data = await api.post('/events', body: {
      'title': title,
      'description': description,
      'venue': venue,
      'starts_at': startsAt.toUtc().toIso8601String(),
      'ends_at': endsAt?.toUtc().toIso8601String(),
      'capacity': capacity,
      'tags': tags,
      'group_id': groupId,
    }) as Map<String, dynamic>;
    return CampusEvent.fromJson(data);
  }

  // --- notifications ------------------------------------------------------

  Future<List<AppNotification>> notifications() async {
    final data = await api.get('/notifications') as Map<String, dynamic>;
    return (data['notifications'] as List)
        .map((e) => AppNotification.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
  }

  Future<void> markAllRead() => api.post('/notifications/read');

  Future<void> markRead(int id) => api.post('/notifications/$id/read');

  // --- people -------------------------------------------------------------

  Future<List<UserCard>> people({String q = '', String? skill, int? batch, String? department}) async {
    final data = await api.get('/people', query: {
      'q': q,
      'skill': skill,
      'batch': batch,
      'department': department,
    }) as Map<String, dynamic>;
    return (data['people'] as List)
        .map((e) => UserCard.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
  }

  Future<Profile> profile(String handle) async =>
      Profile.fromJson(await api.get('/people/$handle') as Map<String, dynamic>);

  Future<Page<Post>> profilePosts(String handle, {int page = 1}) async {
    final data =
        await api.get('/people/$handle/posts', query: {'page': page}) as Map<String, dynamic>;
    return Page(
      items: (data['posts'] as List).map((e) => Post.fromJson((e as Map).cast<String, dynamic>())).toList(),
      hasMore: data['has_more'] == true,
      page: page,
    );
  }

  Future<Map<String, dynamic>> search(String q) async =>
      (await api.get('/search', query: {'q': q})) as Map<String, dynamic>;

  Future<Map<String, dynamic>> updateProfile(Map<String, dynamic> changes) async =>
      (await api.patch('/me', body: changes)) as Map<String, dynamic>;

  Future<void> addSkill(String name) => api.post('/me/skills', query: {'name': name});

  Future<void> removeSkill(int id) => api.delete('/me/skills/$id');

  Future<Map<String, dynamic>> reputation() async =>
      (await api.get('/me/reputation')) as Map<String, dynamic>;

  // --- home chrome ---------------------------------------------------------

  Future<Map<String, dynamic>> home() async => (await api.get('/home')) as Map<String, dynamic>;

  // --- verification --------------------------------------------------------

  Future<VerificationStatus> verificationStatus() async =>
      VerificationStatus.fromJson(await api.get('/verify/status') as Map<String, dynamic>);

  Future<Map<String, dynamic>> submitCard(String filePath) async =>
      (await api.upload('/verify/card', field: 'card', filePath: filePath, fields: {
        'live_capture': 'true',
      })) as Map<String, dynamic>;

  Future<Map<String, dynamic>> submitSelfie(String filePath) async =>
      (await api.upload('/verify/selfie', field: 'selfie', filePath: filePath))
          as Map<String, dynamic>;

  Future<void> disputeVerification(String note) =>
      api.post('/verify/dispute', body: {'note': note});

  // --- account -------------------------------------------------------------

  Future<List<Map<String, dynamic>>> sessions() async =>
      ((await api.get('/auth/sessions')) as List).cast<Map<String, dynamic>>();

  Future<void> revokeSession(int id) => api.post('/auth/sessions/$id/revoke');

  Future<void> changePassword(String current, String password) =>
      api.post('/auth/password', body: {'current': current, 'password': password});
}
