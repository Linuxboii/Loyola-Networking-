import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/session.dart';
import 'events_screen.dart';
import 'feed_screen.dart';
import 'notifications_screen.dart';
import 'profile_screen.dart';
import 'qa_screen.dart';

/// The five places a student actually goes.
///
/// Tabs keep their state across switches (an IndexedStack, not a rebuild), so
/// scrolling the feed, checking a notification and coming back does not lose
/// your place.
class ShellScreen extends StatefulWidget {
  const ShellScreen({super.key});

  @override
  State<ShellScreen> createState() => _ShellScreenState();
}

class _ShellScreenState extends State<ShellScreen> {
  int _index = 0;
  Timer? _badgeTimer;

  @override
  void initState() {
    super.initState();
    // Notifications are polled, not pushed: a campus network does not need to
    // buzz in anyone's pocket, and this avoids shipping a Firebase dependency
    // the college would have to administer.
    _badgeTimer = Timer.periodic(
      const Duration(seconds: 45),
      (_) => context.read<Session>().refreshUnread(),
    );
  }

  @override
  void dispose() {
    _badgeTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final unread = context.select<Session, int>((s) => s.unread);

    return Scaffold(
      body: IndexedStack(
        index: _index,
        children: const [
          FeedScreen(),
          QaScreen(),
          EventsScreen(),
          NotificationsScreen(),
          ProfileScreen(),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (value) {
          setState(() => _index = value);
          if (value == 3) context.read<Session>().refreshUnread();
        },
        destinations: [
          const NavigationDestination(
            icon: Icon(Icons.dynamic_feed_outlined),
            selectedIcon: Icon(Icons.dynamic_feed_rounded),
            label: 'Feed',
          ),
          const NavigationDestination(
            icon: Icon(Icons.help_outline_rounded),
            selectedIcon: Icon(Icons.help_rounded),
            label: 'Q&A',
          ),
          const NavigationDestination(
            icon: Icon(Icons.event_outlined),
            selectedIcon: Icon(Icons.event_rounded),
            label: 'Events',
          ),
          NavigationDestination(
            icon: Badge(
              isLabelVisible: unread > 0,
              label: Text(unread > 99 ? '99+' : '$unread'),
              child: const Icon(Icons.notifications_none_rounded),
            ),
            selectedIcon: Badge(
              isLabelVisible: unread > 0,
              label: Text(unread > 99 ? '99+' : '$unread'),
              child: const Icon(Icons.notifications_rounded),
            ),
            label: 'Alerts',
          ),
          const NavigationDestination(
            icon: Icon(Icons.person_outline_rounded),
            selectedIcon: Icon(Icons.person_rounded),
            label: 'You',
          ),
        ],
      ),
    );
  }
}
