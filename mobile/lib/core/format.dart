import 'package:intl/intl.dart';

/// Compact relative time — "4m", "3h", "2d", then a date.
String relativeTime(DateTime? when) {
  if (when == null) return '';
  final delta = DateTime.now().difference(when.toLocal());
  if (delta.inSeconds < 60) return 'now';
  if (delta.inMinutes < 60) return '${delta.inMinutes}m';
  if (delta.inHours < 24) return '${delta.inHours}h';
  if (delta.inDays < 7) return '${delta.inDays}d';
  if (delta.inDays < 365) return DateFormat('d MMM').format(when.toLocal());
  return DateFormat('MMM y').format(when.toLocal());
}

String eventDate(DateTime when) => DateFormat('EEE d MMM • h:mm a').format(when.toLocal());

String longDate(DateTime when) => DateFormat('d MMMM y').format(when.toLocal());

/// 1200 -> 1.2k, so counts never blow a row's layout.
String compactCount(num value) {
  if (value.abs() < 1000) return '$value';
  if (value.abs() < 1000000) {
    final k = value / 1000;
    return '${k.toStringAsFixed(k.abs() < 10 ? 1 : 0)}k';
  }
  return '${(value / 1000000).toStringAsFixed(1)}m';
}

DateTime? parseTime(dynamic value) {
  if (value == null) return null;
  return DateTime.tryParse('$value');
}

String initialsOf(String name) {
  final parts = name.trim().split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
  if (parts.isEmpty) return '?';
  if (parts.length == 1) return parts.first.characters.first.toUpperCase();
  return (parts.first.characters.first + parts.last.characters.first).toUpperCase();
}

extension _Chars on String {
  Iterable<String> get characters => split('');
}
