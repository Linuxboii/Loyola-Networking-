import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:provider/provider.dart';

import '../core/config.dart';
import '../core/format.dart';
import '../core/session.dart';
import '../core/theme_controller.dart';
import '../core/updater.dart';
import '../data/repository.dart';
import '../widgets/common.dart';
import '../widgets/update_prompt.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  List<Map<String, dynamic>> _sessions = const [];
  String _versionLabel = '—';
  String _updateSubtitle = 'You are up to date as far as we last checked.';
  bool _checkingUpdate = false;

  @override
  void initState() {
    super.initState();
    _loadSessions();
    _loadVersion();
  }

  Future<void> _loadVersion() async {
    final info = await PackageInfo.fromPlatform();
    if (mounted) {
      setState(() => _versionLabel =
          'Version ${info.version} (build ${info.buildNumber})');
    }
  }

  /// The manual check. Unlike the one on launch it ignores the six-hour
  /// throttle and says something either way — a student who taps "check for
  /// updates" and gets silence assumes the button is broken.
  Future<void> _checkForUpdates() async {
    setState(() => _checkingUpdate = true);
    final updater = context.read<Updater>();
    final status = await updater.check(force: true);
    if (!mounted) return;
    setState(() => _checkingUpdate = false);

    if (status == null) {
      setState(() => _updateSubtitle =
          'Could not reach the server. Try again on a connection.');
      return;
    }
    if (status.updateAvailable || status.unsupported) {
      setState(() => _updateSubtitle =
          'Version ${status.latest?.version ?? ''} is available.');
      await UpdatePrompt.show(context, status);
    } else {
      setState(() => _updateSubtitle =
          'Up to date — build ${status.currentBuild} is the newest.');
    }
  }

  Future<void> _loadSessions() async {
    try {
      final sessions = await context.read<Repository>().sessions();
      if (mounted) setState(() => _sessions = sessions);
    } catch (_) {
      // The device list is informational; failing to load it is not worth a toast.
    }
  }

  Future<void> _setPrivacy(String key, bool value) async {
    try {
      await context.read<Repository>().updateProfile({key: value});
      if (mounted) await context.read<Session>().refreshMe();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _changeUsername() async {
    final controller = TextEditingController(
        text: context.read<Session>().me?.card.handle ?? '');
    final save = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: const Text('Change username'),
              content: TextField(
                  controller: controller,
                  autofocus: true,
                  autocorrect: false,
                  decoration: const InputDecoration(
                      prefixText: '@',
                      helperText: '3-24 letters, numbers, or underscores.')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('Cancel')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('Save'))
              ],
            ));
    if (save != true || !mounted) return;
    try {
      await context
          .read<Repository>()
          .updateProfile({'handle': controller.text.trim()});
      if (!mounted) return;
      await context.read<Session>().refreshMe();
      if (mounted) showMessage(context, 'Username changed.');
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _changePassword() async {
    final current = TextEditingController();
    final next = TextEditingController();

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Change password'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: current,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Current password'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: next,
              obscureText: true,
              decoration: const InputDecoration(
                labelText: 'New password',
                helperText:
                    'At least 10 characters, with a letter and a number.',
                helperMaxLines: 2,
              ),
            ),
            const SizedBox(height: 12),
            Text(
              'Every other signed-in device is signed out.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Change')),
        ],
      ),
    );

    if (confirmed != true || !mounted) return;
    try {
      await context.read<Repository>().changePassword(current.text, next.text);
      if (!mounted) return;
      showMessage(context, 'Password changed.');
      await _loadSessions();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _revoke(Map<String, dynamic> session) async {
    try {
      await context.read<Repository>().revokeSession(session['id'] as int);
      await _loadSessions();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _signOut() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Sign out?'),
        content: const Text(
            'You will need your username and password to get back in.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Sign out')),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    await context.read<Session>().logout();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final me = context.watch<Session>().me;
    final canManageServer = me?.roles.any((role) =>
            role == 'moderator' || role == 'admin' || role == 'super_admin') ??
        false;

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.only(bottom: 40),
        children: [
          const SectionHeader(title: 'Account'),
          ListTile(
            leading: const Icon(Icons.alternate_email_rounded),
            title: const Text('Username'),
            subtitle: Text('@${me?.card.handle ?? ''}'),
            trailing: const Icon(Icons.edit_outlined),
            onTap: _changeUsername,
          ),
          if (me?.rollNumber != null)
            ListTile(
              leading: const Icon(Icons.badge_outlined),
              title: const Text('Roll number'),
              subtitle: Text(me!.rollNumber!),
            ),
          ListTile(
            leading: const Icon(Icons.verified_user_outlined),
            title: const Text('Verification'),
            subtitle: Text(me?.tierLabel ?? ''),
          ),
          ListTile(
            leading: const Icon(Icons.password_rounded),
            title: const Text('Change password'),
            onTap: _changePassword,
            trailing: const Icon(Icons.chevron_right_rounded),
          ),
          const SectionHeader(title: 'Privacy'),
          SwitchListTile(
            secondary: const Icon(Icons.search_off_rounded),
            value: me?.hideFromSearch ?? false,
            onChanged: (value) => _setPrivacy('hide_from_search', value),
            title: const Text('Hide me from search'),
            subtitle: const Text(
                'You stay reachable to people who already know your username.'),
          ),
          SwitchListTile(
            secondary: const Icon(Icons.history_toggle_off_rounded),
            value: me?.hideActivity ?? false,
            onChanged: (value) => _setPrivacy('hide_activity', value),
            title: const Text('Hide my activity'),
            subtitle: const Text(
                'Your posts and answers stop appearing on your profile.'),
          ),
          const SectionHeader(title: 'Appearance'),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: DropdownButtonFormField<AppThemeChoice>(
              initialValue: context.watch<ThemeController>().choice,
              decoration: const InputDecoration(labelText: 'Theme'),
              items: const [
                DropdownMenuItem(
                    value: AppThemeChoice.system,
                    child: Text('Use device setting')),
                DropdownMenuItem(
                    value: AppThemeChoice.light, child: Text('Light')),
                DropdownMenuItem(
                    value: AppThemeChoice.amoled,
                    child: Text('AMOLED dark · true black')),
              ],
              onChanged: (v) {
                if (v != null) context.read<ThemeController>().setChoice(v);
              },
            ),
          ),
          const SectionHeader(title: 'Signed-in devices'),
          if (_sessions.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16),
              child: Text('No other devices.'),
            ),
          for (final session in _sessions)
            ListTile(
              leading: Icon(
                session['current'] == true
                    ? Icons.phone_android_rounded
                    : Icons.devices_other_rounded,
              ),
              title: Text(
                _deviceName('${session['user_agent'] ?? ''}'),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              subtitle: Text(
                '${session['current'] == true ? 'This device · ' : ''}'
                'since ${relativeTime(DateTime.tryParse('${session['created_at']}'))}',
              ),
              trailing: session['current'] == true
                  ? null
                  : TextButton(
                      onPressed: () => _revoke(session),
                      child: const Text('Revoke')),
            ),
          const SectionHeader(title: 'About'),
          if (canManageServer)
            ListTile(
              leading: const Icon(Icons.dns_outlined),
              title: const Text('Server'),
              subtitle: Text(AppConfig.baseUrl),
            ),
          ListTile(
            leading: const Icon(Icons.info_outline_rounded),
            title: const Text('Loyola Networking'),
            subtitle: Text(_versionLabel),
          ),
          if (Updater.supported)
            ListTile(
              leading: const Icon(Icons.system_update_rounded),
              title: const Text('Check for updates'),
              subtitle: Text(_updateSubtitle),
              trailing: _checkingUpdate
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.chevron_right_rounded),
              onTap: _checkingUpdate ? null : _checkForUpdates,
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 20),
            child: Text(
              'Your ID card image is encrypted, seen only by the reviewer who approves '
              'you, and deleted automatically. Moderation actions against your account '
              'are logged and appealable.',
              style: theme.textTheme.bodySmall,
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: OutlinedButton.icon(
              onPressed: _signOut,
              icon: const Icon(Icons.logout_rounded, size: 18),
              label: const Text('Sign out'),
              style: OutlinedButton.styleFrom(
                  foregroundColor: theme.colorScheme.error),
            ),
          ),
        ],
      ),
    );
  }

  /// User agents are noise; show the part a student would recognise.
  static String _deviceName(String userAgent) {
    if (userAgent.isEmpty) return 'Unknown device';
    if (userAgent.contains('Dart') || userAgent.contains('okhttp')) {
      return 'Loyola app (Android)';
    }
    for (final marker in [
      'Android',
      'iPhone',
      'iPad',
      'Windows',
      'Macintosh',
      'Linux'
    ]) {
      if (userAgent.contains(marker)) return '$marker browser';
    }
    return userAgent.length > 40 ? '${userAgent.substring(0, 40)}…' : userAgent;
  }
}
