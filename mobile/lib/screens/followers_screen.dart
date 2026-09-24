import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../data/repository.dart';
import '../widgets/common.dart';

class FollowersScreen extends StatefulWidget {
  const FollowersScreen({super.key});
  @override
  State<FollowersScreen> createState() => _FollowersScreenState();
}

class _FollowersScreenState extends State<FollowersScreen> {
  Map<String, dynamic>? _data;
  String? _error;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await context.read<Repository>().follows();
      if (mounted) {
        setState(() {
          _data = data;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  List<Map<String, dynamic>> _rows(String key) =>
      ((_data?[key] as List?) ?? const [])
          .map((e) => (e as Map).cast<String, dynamic>())
          .toList();
  Future<void> _decide(int id, String action) async {
    await context.read<Repository>().decideFollow(id, action);
    await _load();
  }

  Future<void> _remove(int id) async {
    await context.read<Repository>().removeFollower(id);
    await _load();
  }

  Widget _tile(Map<String, dynamic> row,
      {bool request = false, bool removable = false}) {
    final user = (row['user'] as Map).cast<String, dynamic>();
    final handle = '@${user['handle']}';
    return ListTile(
        title: Text(handle),
        trailing: request
            ? Wrap(children: [
                IconButton(
                    tooltip: 'Approve',
                    onPressed: () =>
                        _decide(row['follow_id'] as int, 'approve'),
                    icon: const Icon(Icons.check_circle_outline)),
                IconButton(
                    tooltip: 'Reject',
                    onPressed: () => _decide(row['follow_id'] as int, 'reject'),
                    icon: const Icon(Icons.cancel_outlined))
              ])
            : removable
                ? TextButton(
                    onPressed: () => _remove(row['follow_id'] as int),
                    child: const Text('Remove'))
                : null);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(title: const Text('Connections')),
      body: _error != null
          ? ErrorView(message: _error!, onRetry: _load)
          : _data == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(children: [
                    const SectionHeader(title: 'Follow requests'),
                    if (_rows('requests').isEmpty)
                      const ListTile(title: Text('No pending requests.')),
                    for (final r in _rows('requests')) _tile(r, request: true),
                    const SectionHeader(title: 'Followers'),
                    for (final r in _rows('followers'))
                      _tile(r, removable: true),
                    const SectionHeader(title: 'Following'),
                    for (final r in _rows('following')) _tile(r),
                    const SectionHeader(title: 'Requested'),
                    for (final r in _rows('outgoing_requests')) _tile(r)
                  ])));
}
