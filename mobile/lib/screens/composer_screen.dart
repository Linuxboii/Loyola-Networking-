import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../core/session.dart';
import '../data/repository.dart';
import '../widgets/common.dart';

/// Write a post: text, an image, a link, or a poll.
class ComposerScreen extends StatefulWidget {
  const ComposerScreen({super.key, this.groupId, this.groupName});

  final int? groupId;
  final String? groupName;

  @override
  State<ComposerScreen> createState() => _ComposerScreenState();
}

class _ComposerScreenState extends State<ComposerScreen> {
  final _title = TextEditingController();
  final _body = TextEditingController();
  final _tags = TextEditingController();
  final _link = TextEditingController();
  final _pollControllers = [TextEditingController(), TextEditingController()];
  final _picker = ImagePicker();

  String _kind = 'text';
  File? _image;
  bool _sending = false;

  @override
  void dispose() {
    _title.dispose();
    _body.dispose();
    _tags.dispose();
    _link.dispose();
    for (final controller in _pollControllers) {
      controller.dispose();
    }
    super.dispose();
  }

  List<String> get _tagList => _tags.text
      .replaceAll(',', ' ')
      .split(RegExp(r'\s+'))
      .map((t) => t.trim())
      .where((t) => t.isNotEmpty)
      .take(6)
      .toList();

  bool get _canSend {
    if (_sending) return false;
    if (_kind == 'poll') {
      final filled = _pollControllers.where((c) => c.text.trim().isNotEmpty).length;
      return _body.text.trim().isNotEmpty && filled >= 2;
    }
    if (_kind == 'image') return _image != null || _body.text.trim().isNotEmpty;
    if (_kind == 'link') return _link.text.trim().isNotEmpty;
    return _body.text.trim().isNotEmpty || _title.text.trim().isNotEmpty;
  }

  Future<void> _pickImage() async {
    final shot = await _picker.pickImage(
      source: ImageSource.gallery,
      maxWidth: 1600,
      imageQuality: 88,
    );
    if (shot != null) {
      setState(() {
        _image = File(shot.path);
        _kind = 'image';
      });
    }
  }

  Future<void> _send() async {
    if (!_canSend) return;
    setState(() => _sending = true);
    final repo = context.read<Repository>();

    try {
      final media = <String>[];
      if (_image != null) {
        media.add(await repo.uploadImage(_image!.path));
      }
      await repo.createPost(
        kind: _kind,
        title: _title.text.trim().isEmpty ? null : _title.text.trim(),
        body: _body.text.trim(),
        tags: _tagList,
        linkUrl: _kind == 'link' ? _link.text.trim() : null,
        groupId: widget.groupId,
        media: media,
        pollOptions: _kind == 'poll'
            ? _pollControllers.map((c) => c.text.trim()).where((t) => t.isNotEmpty).toList()
            : const [],
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (error) {
      if (mounted) {
        showError(context, error);
        setState(() => _sending = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final me = context.read<Session>().me;
    final canPoll = me?.can('create_poll') ?? false;

    return Scaffold(
      appBar: AppBar(
        title: Text(widget.groupName == null ? 'New post' : 'Post to ${widget.groupName}'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: FilledButton(
              onPressed: _canSend ? _send : null,
              style: FilledButton.styleFrom(minimumSize: const Size(72, 38)),
              child: _sending
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                    )
                  : const Text('Post'),
            ),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 40),
        children: [
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                for (final option in [
                  ('text', 'Text', Icons.notes_rounded),
                  ('image', 'Photo', Icons.image_outlined),
                  ('link', 'Link', Icons.link_rounded),
                  if (canPoll) ('poll', 'Poll', Icons.poll_outlined),
                ])
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      selected: _kind == option.$1,
                      onSelected: (_) => setState(() => _kind = option.$1),
                      avatar: Icon(option.$3, size: 17),
                      label: Text(option.$2),
                    ),
                  ),
              ],
            ),
          ),
          if (!canPoll)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Text(
                'Polls unlock at Contributor standing.',
                style: theme.textTheme.bodySmall,
              ),
            ),
          const SizedBox(height: 18),
          TextField(
            controller: _title,
            textCapitalization: TextCapitalization.sentences,
            inputFormatters: [LengthLimitingTextInputFormatter(200)],
            decoration: const InputDecoration(
              labelText: 'Title (optional)',
              border: InputBorder.none,
              filled: false,
              contentPadding: EdgeInsets.zero,
            ),
            style: theme.textTheme.titleLarge,
            onChanged: (_) => setState(() {}),
          ),
          const Divider(height: 24),
          TextField(
            controller: _body,
            autofocus: true,
            minLines: 5,
            maxLines: null,
            textCapitalization: TextCapitalization.sentences,
            inputFormatters: [LengthLimitingTextInputFormatter(8000)],
            decoration: InputDecoration(
              hintText: _kind == 'poll'
                  ? 'What are you asking campus to decide?'
                  : 'Share something useful with campus…',
              border: InputBorder.none,
              filled: false,
              contentPadding: EdgeInsets.zero,
            ),
            style: theme.textTheme.bodyLarge,
            onChanged: (_) => setState(() {}),
          ),
          if (_kind == 'link') ...[
            const SizedBox(height: 16),
            TextField(
              controller: _link,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(
                labelText: 'Link',
                hintText: 'https://',
                prefixIcon: Icon(Icons.link_rounded),
              ),
              onChanged: (_) => setState(() {}),
            ),
          ],
          if (_kind == 'poll') ...[
            const SizedBox(height: 16),
            for (var i = 0; i < _pollControllers.length; i++)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: TextField(
                  controller: _pollControllers[i],
                  inputFormatters: [LengthLimitingTextInputFormatter(120)],
                  decoration: InputDecoration(
                    labelText: 'Option ${i + 1}',
                    suffixIcon: i >= 2
                        ? IconButton(
                            icon: const Icon(Icons.close_rounded, size: 18),
                            onPressed: () => setState(() {
                              _pollControllers.removeAt(i).dispose();
                            }),
                          )
                        : null,
                  ),
                  onChanged: (_) => setState(() {}),
                ),
              ),
            if (_pollControllers.length < 6)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: () => setState(() => _pollControllers.add(TextEditingController())),
                  icon: const Icon(Icons.add_rounded, size: 18),
                  label: const Text('Add option'),
                ),
              ),
          ],
          if (_image != null) ...[
            const SizedBox(height: 16),
            Stack(
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(12),
                  child: Image.file(_image!, width: double.infinity, fit: BoxFit.cover),
                ),
                Positioned(
                  top: 8,
                  right: 8,
                  child: IconButton.filled(
                    onPressed: () => setState(() => _image = null),
                    icon: const Icon(Icons.close_rounded, size: 18),
                    style: IconButton.styleFrom(backgroundColor: Colors.black54),
                  ),
                ),
              ],
            ),
          ],
          const SizedBox(height: 20),
          TextField(
            controller: _tags,
            decoration: const InputDecoration(
              labelText: 'Tags',
              hintText: 'placements exams robotics',
              helperText: 'Up to six. They are how people find this later.',
              prefixIcon: Icon(Icons.tag_rounded),
            ),
            onChanged: (_) => setState(() {}),
          ),
          if (_tagList.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: _tagList.map((tag) => TagChip(tag: tag.toLowerCase())).toList(),
            ),
          ],
          const SizedBox(height: 20),
          OutlinedButton.icon(
            onPressed: _pickImage,
            icon: const Icon(Icons.add_photo_alternate_outlined, size: 19),
            label: Text(_image == null ? 'Attach a photo' : 'Replace photo'),
          ),
          const SizedBox(height: 24),
          Text(
            'Posts are public to every verified student. The community rules apply, '
            'and moderation is logged.',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
