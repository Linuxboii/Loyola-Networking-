import '../core/format.dart';

/// Plain data classes mirroring the JSON the API returns.
///
/// They are deliberately forgiving: a field the server stops sending must not
/// crash an older build in a student's pocket, so every read has a default.

class UserCard {
  UserCard({
    required this.id,
    required this.handle,
    required this.fullName,
    this.photoUrl,
    this.repTier,
    this.repTotal = 0,
    this.batchYear,
    this.department,
    this.course,
    this.isModerator = false,
    this.anonymous = false,
  });

  final int? id;
  final String? handle;
  final String fullName;
  final String? photoUrl;
  final String? repTier;
  final double repTotal;
  final int? batchYear;
  final String? department;
  final String? course;
  final bool isModerator;
  final bool anonymous;

  String get initials => initialsOf(fullName);

  String get subtitle {
    final bits = <String>[
      if (handle != null) '@$handle',
      if (department != null && department!.isNotEmpty) department!,
      if (batchYear != null) "'${batchYear! % 100}",
    ];
    return bits.join(' · ');
  }

  factory UserCard.fromJson(Map<String, dynamic> json) => UserCard(
        id: json['id'] as int?,
        handle: json['handle'] as String?,
        fullName: (json['full_name'] ?? 'Unknown') as String,
        photoUrl: json['photo_url'] as String?,
        repTier: json['rep_tier'] as String?,
        repTotal: (json['rep_total'] as num?)?.toDouble() ?? 0,
        batchYear: json['batch_year'] as int?,
        department: json['department'] as String?,
        course: json['course'] as String?,
        isModerator: json['is_moderator'] == true,
        anonymous: json['anonymous'] == true,
      );
}

class Me {
  Me({
    required this.card,
    required this.tier,
    required this.tierLabel,
    required this.status,
    required this.bio,
    required this.interests,
    required this.links,
    required this.roles,
    required this.isAdmin,
    required this.capabilities,
    required this.repPillars,
    required this.unread,
    this.rollNumber,
    this.hideFromSearch = false,
    this.hideActivity = false,
    this.endorsePolicy = 'anyone',
    this.recoveryEmail,
  });

  final UserCard card;
  final int tier;
  final String tierLabel;
  final String status;
  final String bio;
  final List<String> interests;
  final Map<String, String> links;
  final List<String> roles;
  final bool isAdmin;
  final Map<String, bool> capabilities;
  final Map<String, double> repPillars;
  final int unread;
  final String? rollNumber;
  final bool hideFromSearch;
  final bool hideActivity;
  final String endorsePolicy;
  final String? recoveryEmail;

  bool get canRead => tier >= 1;
  bool get canWrite => tier >= 2;
  bool get isSuspended => status == 'suspended';
  bool can(String capability) => capabilities[capability] ?? false;

  factory Me.fromJson(Map<String, dynamic> json) => Me(
        card: UserCard.fromJson(json),
        tier: (json['tier'] as num?)?.toInt() ?? 0,
        tierLabel: (json['tier_label'] ?? '') as String,
        status: (json['status'] ?? 'active') as String,
        bio: (json['bio'] ?? '') as String,
        interests: ((json['interests'] as List?) ?? const []).map((e) => '$e').toList(),
        links: ((json['links'] as Map?) ?? const {})
            .map((key, value) => MapEntry('$key', '$value')),
        roles: ((json['roles'] as List?) ?? const []).map((e) => '$e').toList(),
        isAdmin: json['is_admin'] == true,
        capabilities: ((json['capabilities'] as Map?) ?? const {})
            .map((key, value) => MapEntry('$key', value == true)),
        repPillars: ((json['rep_pillars'] as Map?) ?? const {})
            .map((key, value) => MapEntry('$key', (value as num?)?.toDouble() ?? 0)),
        unread: (json['unread_notifications'] as num?)?.toInt() ?? 0,
        rollNumber: json['roll_number'] as String?,
        hideFromSearch: json['hide_from_search'] == true,
        hideActivity: json['hide_activity'] == true,
        endorsePolicy: (json['endorse_policy'] ?? 'anyone') as String,
        recoveryEmail: json['recovery_email'] as String?,
      );
}

class PollOption {
  PollOption({required this.id, required this.label, required this.votes, required this.share});

  final int id;
  final String label;
  final int votes;
  final int share;

  factory PollOption.fromJson(Map<String, dynamic> json) => PollOption(
        id: json['id'] as int,
        label: (json['label'] ?? '') as String,
        votes: (json['votes'] as num?)?.toInt() ?? 0,
        share: (json['share'] as num?)?.toInt() ?? 0,
      );
}

class Post {
  Post({
    required this.id,
    required this.kind,
    required this.body,
    required this.tags,
    required this.media,
    required this.score,
    required this.commentCount,
    required this.author,
    required this.myVote,
    this.title,
    this.linkUrl,
    this.groupId,
    this.isOfficial = false,
    this.createdAt,
    this.editedAt,
    this.poll,
    this.myPollOption,
  });

  final int id;
  final String kind;
  final String? title;
  final String body;
  final List<String> tags;
  final List<String> media;
  final String? linkUrl;
  final int? groupId;
  final bool isOfficial;
  final int score;
  final int commentCount;
  final DateTime? createdAt;
  final DateTime? editedAt;
  final UserCard author;
  final int myVote;
  final List<PollOption>? poll;
  final int? myPollOption;

  Post copyWith({int? score, int? myVote, int? commentCount, List<PollOption>? poll, int? myPollOption}) =>
      Post(
        id: id,
        kind: kind,
        title: title,
        body: body,
        tags: tags,
        media: media,
        linkUrl: linkUrl,
        groupId: groupId,
        isOfficial: isOfficial,
        score: score ?? this.score,
        commentCount: commentCount ?? this.commentCount,
        createdAt: createdAt,
        editedAt: editedAt,
        author: author,
        myVote: myVote ?? this.myVote,
        poll: poll ?? this.poll,
        myPollOption: myPollOption ?? this.myPollOption,
      );

  factory Post.fromJson(Map<String, dynamic> json) => Post(
        id: json['id'] as int,
        kind: (json['kind'] ?? 'text') as String,
        title: json['title'] as String?,
        body: (json['body'] ?? '') as String,
        tags: ((json['tags'] as List?) ?? const []).map((e) => '$e').toList(),
        media: ((json['media'] as List?) ?? const [])
            .where((e) => e != null)
            .map((e) => '$e')
            .toList(),
        linkUrl: json['link_url'] as String?,
        groupId: json['group_id'] as int?,
        isOfficial: json['is_official'] == true,
        score: (json['score'] as num?)?.toInt() ?? 0,
        commentCount: (json['comment_count'] as num?)?.toInt() ?? 0,
        createdAt: parseTime(json['created_at']),
        editedAt: parseTime(json['edited_at']),
        author: UserCard.fromJson((json['author'] as Map).cast<String, dynamic>()),
        myVote: (json['my_vote'] as num?)?.toInt() ?? 0,
        poll: (json['poll'] as List?)
            ?.map((e) => PollOption.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        myPollOption: json['my_poll_option'] as int?,
      );
}

class Comment {
  Comment({
    required this.id,
    required this.postId,
    required this.body,
    required this.score,
    required this.author,
    required this.myVote,
    this.parentId,
    this.createdAt,
  });

  final int id;
  final int postId;
  final int? parentId;
  final String body;
  final int score;
  final DateTime? createdAt;
  final UserCard author;
  final int myVote;

  Comment copyWith({int? score, int? myVote}) => Comment(
        id: id,
        postId: postId,
        parentId: parentId,
        body: body,
        score: score ?? this.score,
        createdAt: createdAt,
        author: author,
        myVote: myVote ?? this.myVote,
      );

  factory Comment.fromJson(Map<String, dynamic> json) => Comment(
        id: json['id'] as int,
        postId: json['post_id'] as int,
        parentId: json['parent_id'] as int?,
        body: (json['body'] ?? '') as String,
        score: (json['score'] as num?)?.toInt() ?? 0,
        createdAt: parseTime(json['created_at']),
        author: UserCard.fromJson((json['author'] as Map).cast<String, dynamic>()),
        myVote: (json['my_vote'] as num?)?.toInt() ?? 0,
      );
}

class Question {
  Question({
    required this.id,
    required this.title,
    required this.body,
    required this.tags,
    required this.score,
    required this.answerCount,
    required this.author,
    required this.myVote,
    this.subject,
    this.semester,
    this.isAnonymous = false,
    this.acceptedAnswerId,
    this.viewCount = 0,
    this.createdAt,
  });

  final int id;
  final String title;
  final String body;
  final List<String> tags;
  final String? subject;
  final String? semester;
  final bool isAnonymous;
  final int? acceptedAnswerId;
  final int score;
  final int answerCount;
  final int viewCount;
  final DateTime? createdAt;
  final UserCard author;
  final int myVote;

  bool get isAnswered => acceptedAnswerId != null;

  Question copyWith({int? score, int? myVote, int? acceptedAnswerId}) => Question(
        id: id,
        title: title,
        body: body,
        tags: tags,
        subject: subject,
        semester: semester,
        isAnonymous: isAnonymous,
        acceptedAnswerId: acceptedAnswerId ?? this.acceptedAnswerId,
        score: score ?? this.score,
        answerCount: answerCount,
        viewCount: viewCount,
        createdAt: createdAt,
        author: author,
        myVote: myVote ?? this.myVote,
      );

  factory Question.fromJson(Map<String, dynamic> json) => Question(
        id: json['id'] as int,
        title: (json['title'] ?? '') as String,
        body: (json['body'] ?? '') as String,
        tags: ((json['tags'] as List?) ?? const []).map((e) => '$e').toList(),
        subject: json['subject'] as String?,
        semester: json['semester'] as String?,
        isAnonymous: json['is_anonymous'] == true,
        acceptedAnswerId: json['accepted_answer_id'] as int?,
        score: (json['score'] as num?)?.toInt() ?? 0,
        answerCount: (json['answer_count'] as num?)?.toInt() ?? 0,
        viewCount: (json['view_count'] as num?)?.toInt() ?? 0,
        createdAt: parseTime(json['created_at']),
        author: UserCard.fromJson((json['author'] as Map).cast<String, dynamic>()),
        myVote: (json['my_vote'] as num?)?.toInt() ?? 0,
      );
}

class Answer {
  Answer({
    required this.id,
    required this.questionId,
    required this.body,
    required this.isAccepted,
    required this.score,
    required this.author,
    required this.myVote,
    this.createdAt,
  });

  final int id;
  final int questionId;
  final String body;
  final bool isAccepted;
  final int score;
  final DateTime? createdAt;
  final UserCard author;
  final int myVote;

  Answer copyWith({int? score, int? myVote, bool? isAccepted}) => Answer(
        id: id,
        questionId: questionId,
        body: body,
        isAccepted: isAccepted ?? this.isAccepted,
        score: score ?? this.score,
        createdAt: createdAt,
        author: author,
        myVote: myVote ?? this.myVote,
      );

  factory Answer.fromJson(Map<String, dynamic> json) => Answer(
        id: json['id'] as int,
        questionId: json['question_id'] as int,
        body: (json['body'] ?? '') as String,
        isAccepted: json['is_accepted'] == true,
        score: (json['score'] as num?)?.toInt() ?? 0,
        createdAt: parseTime(json['created_at']),
        author: UserCard.fromJson((json['author'] as Map).cast<String, dynamic>()),
        myVote: (json['my_vote'] as num?)?.toInt() ?? 0,
      );
}

class Group {
  Group({
    required this.id,
    required this.slug,
    required this.name,
    required this.description,
    required this.kind,
    required this.memberCount,
    this.isOfficial = false,
    this.bannerUrl,
    this.clubRep = 0,
    this.myRole,
  });

  final int id;
  final String slug;
  final String name;
  final String description;
  final String kind;
  final bool isOfficial;
  final String? bannerUrl;
  final int memberCount;
  final double clubRep;
  final String? myRole;

  bool get joined => myRole != null;

  factory Group.fromJson(Map<String, dynamic> json) => Group(
        id: json['id'] as int,
        slug: (json['slug'] ?? '') as String,
        name: (json['name'] ?? '') as String,
        description: (json['description'] ?? '') as String,
        kind: (json['kind'] ?? 'interest') as String,
        isOfficial: json['is_official'] == true,
        bannerUrl: json['banner_url'] as String?,
        memberCount: (json['member_count'] as num?)?.toInt() ?? 0,
        clubRep: (json['club_rep'] as num?)?.toDouble() ?? 0,
        myRole: json['my_role'] as String?,
      );
}

class CampusEvent {
  CampusEvent({
    required this.id,
    required this.title,
    required this.description,
    required this.venue,
    required this.startsAt,
    required this.tags,
    required this.rsvpCount,
    required this.host,
    this.endsAt,
    this.capacity,
    this.isOfficial = false,
    this.checkinCount = 0,
    this.groupId,
    this.myRsvpState,
    this.myTicketCode,
    this.checkedInAt,
  });

  final int id;
  final String title;
  final String description;
  final String venue;
  final DateTime startsAt;
  final DateTime? endsAt;
  final int? capacity;
  final List<String> tags;
  final bool isOfficial;
  final int rsvpCount;
  final int checkinCount;
  final int? groupId;
  final UserCard host;
  final String? myRsvpState;
  final String? myTicketCode;
  final DateTime? checkedInAt;

  bool get isGoing => myRsvpState == 'going';
  bool get isFull => capacity != null && rsvpCount >= capacity!;

  factory CampusEvent.fromJson(Map<String, dynamic> json) {
    final rsvp = (json['my_rsvp'] as Map?)?.cast<String, dynamic>();
    return CampusEvent(
      id: json['id'] as int,
      title: (json['title'] ?? '') as String,
      description: (json['description'] ?? '') as String,
      venue: (json['venue'] ?? '') as String,
      startsAt: parseTime(json['starts_at']) ?? DateTime.now(),
      endsAt: parseTime(json['ends_at']),
      capacity: json['capacity'] as int?,
      tags: ((json['tags'] as List?) ?? const []).map((e) => '$e').toList(),
      isOfficial: json['is_official'] == true,
      rsvpCount: (json['rsvp_count'] as num?)?.toInt() ?? 0,
      checkinCount: (json['checkin_count'] as num?)?.toInt() ?? 0,
      groupId: json['group_id'] as int?,
      host: UserCard.fromJson((json['host'] as Map).cast<String, dynamic>()),
      myRsvpState: rsvp?['state'] as String?,
      myTicketCode: rsvp?['ticket_code'] as String?,
      checkedInAt: parseTime(rsvp?['checked_in_at']),
    );
  }
}

class AppNotification {
  AppNotification({
    required this.id,
    required this.kind,
    required this.title,
    required this.body,
    required this.link,
    required this.read,
    this.createdAt,
  });

  final int id;
  final String kind;
  final String title;
  final String body;
  final String link;
  final bool read;
  final DateTime? createdAt;

  factory AppNotification.fromJson(Map<String, dynamic> json) => AppNotification(
        id: json['id'] as int,
        kind: (json['kind'] ?? '') as String,
        title: (json['title'] ?? '') as String,
        body: (json['body'] ?? '') as String,
        link: (json['link'] ?? '/') as String,
        read: json['read'] == true,
        createdAt: parseTime(json['created_at']),
      );
}

class ProfileSkill {
  ProfileSkill({
    required this.id,
    required this.name,
    required this.endorsements,
    required this.endorsedByMe,
  });

  final int id;
  final String name;
  final int endorsements;
  final bool endorsedByMe;

  factory ProfileSkill.fromJson(Map<String, dynamic> json) => ProfileSkill(
        id: json['id'] as int,
        name: (json['name'] ?? '') as String,
        endorsements: (json['endorsements'] as num?)?.toInt() ?? 0,
        endorsedByMe: json['endorsed_by_me'] == true,
      );
}

class Profile {
  Profile({
    required this.card,
    required this.bio,
    required this.interests,
    required this.links,
    required this.skills,
    required this.counts,
    required this.repPillars,
    required this.isMe,
    required this.activityHidden,
    this.displayYear = '',
    this.joinedAt,
  });

  final UserCard card;
  final String bio;
  final List<String> interests;
  final Map<String, String> links;
  final List<ProfileSkill> skills;
  final Map<String, int> counts;
  final Map<String, double> repPillars;
  final bool isMe;
  final bool activityHidden;
  final String displayYear;
  final DateTime? joinedAt;

  factory Profile.fromJson(Map<String, dynamic> json) => Profile(
        card: UserCard.fromJson(json),
        bio: (json['bio'] ?? '') as String,
        interests: ((json['interests'] as List?) ?? const []).map((e) => '$e').toList(),
        links: ((json['links'] as Map?) ?? const {}).map((k, v) => MapEntry('$k', '$v')),
        skills: ((json['skills'] as List?) ?? const [])
            .map((e) => ProfileSkill.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        counts: ((json['counts'] as Map?) ?? const {})
            .map((k, v) => MapEntry('$k', (v as num?)?.toInt() ?? 0)),
        repPillars: ((json['rep_pillars'] as Map?) ?? const {})
            .map((k, v) => MapEntry('$k', (v as num?)?.toDouble() ?? 0)),
        isMe: json['is_me'] == true,
        activityHidden: json['activity_hidden'] == true,
        displayYear: (json['display_year'] ?? '') as String,
        joinedAt: parseTime(json['joined_at']),
      );
}

class VerificationStatus {
  VerificationStatus({
    required this.tier,
    required this.nextStep,
    required this.attemptsUsed,
    required this.attemptsAllowed,
    required this.retentionDays,
    this.recordStatus,
    this.extracted = const {},
    this.decisionReason,
    this.queuePosition,
    this.confidence = 0,
  });

  final int tier;
  final String nextStep;
  final int attemptsUsed;
  final int attemptsAllowed;
  final int retentionDays;
  final String? recordStatus;
  final Map<String, dynamic> extracted;
  final String? decisionReason;
  final int? queuePosition;
  final double confidence;

  int get attemptsLeft => (attemptsAllowed - attemptsUsed).clamp(0, attemptsAllowed);

  factory VerificationStatus.fromJson(Map<String, dynamic> json) {
    final record = (json['record'] as Map?)?.cast<String, dynamic>();
    return VerificationStatus(
      tier: (json['tier'] as num?)?.toInt() ?? 0,
      nextStep: (json['next_step'] ?? 'capture_card') as String,
      attemptsUsed: (json['attempts_used'] as num?)?.toInt() ?? 0,
      attemptsAllowed: (json['attempts_allowed'] as num?)?.toInt() ?? 5,
      retentionDays: (json['retention_days'] as num?)?.toInt() ?? 30,
      recordStatus: record?['status'] as String?,
      extracted: ((record?['extracted'] as Map?) ?? const {}).cast<String, dynamic>(),
      decisionReason: record?['decision_reason'] as String?,
      queuePosition: json['queue_position'] as int?,
      confidence: (record?['confidence'] as num?)?.toDouble() ?? 0,
    );
  }
}
