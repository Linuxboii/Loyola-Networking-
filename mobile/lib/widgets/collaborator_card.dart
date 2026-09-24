import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

typedef CollaboratorLinkOpener = Future<bool> Function(Uri uri);

const collaboratorRepository =
    'https://github.com/Linuxboii/Loyola-Networking-';
const collaboratorEmail = 'ksushanth477@gmail.com';

class CollaboratorCard extends StatelessWidget {
  const CollaboratorCard({
    super.key,
    this.onOpen = _openExternally,
  });

  final CollaboratorLinkOpener onOpen;

  static Future<bool> _openExternally(Uri uri) =>
      launchUrl(uri, mode: LaunchMode.externalApplication);

  Future<void> _open(BuildContext context, Uri uri) async {
    Navigator.pop(context);
    final opened = await onOpen(uri);
    if (!opened && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Could not open that link.')),
      );
    }
  }

  void _showDetails(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(24, 4, 24, 28),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                'Build Loyola Networking with us',
                style: Theme.of(sheetContext).textTheme.headlineSmall,
              ),
              const SizedBox(height: 10),
              const Text(
                'This project is open source. Serious contributors can help '
                'with Flutter and Android, FastAPI, UI/UX, accessibility, '
                'testing, documentation, privacy, or security.',
              ),
              const SizedBox(height: 16),
              const Text(
                'Start by reading the repository and its security policy. Pick '
                'one specific improvement, open a clear issue or pull request, '
                'and never include student data, credentials, or ID images.',
              ),
              const SizedBox(height: 16),
              const Text(
                'If you are genuinely interested in contributing long-term, '
                'email Sushanth with what you want to work on and links to any '
                'relevant work.',
              ),
              const SizedBox(height: 24),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: () => _open(
                    sheetContext,
                    Uri.parse(collaboratorRepository),
                  ),
                  icon: const Icon(Icons.code_rounded),
                  label: const Text('View GitHub repository'),
                ),
              ),
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed: () => _open(
                    sheetContext,
                    Uri(
                      scheme: 'mailto',
                      path: collaboratorEmail,
                      queryParameters: const {
                        'subject': 'Loyola Networking collaboration',
                      },
                    ),
                  ),
                  icon: const Icon(Icons.mail_outline_rounded),
                  label: const Text('Contact Sushanth'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: const Icon(Icons.handshake_outlined),
      title: const Text('Become a collaborator'),
      subtitle: const Text('Help improve the open-source app'),
      trailing: const Icon(Icons.chevron_right_rounded),
      onTap: () => _showDetails(context),
    );
  }
}
