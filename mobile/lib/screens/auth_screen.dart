import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/session.dart';
import 'splash_screen.dart';

/// Sign in and sign up on one screen.
///
/// Registration asks for nothing but a username, a name and a password — the ID
/// card supplies the real identity later, and asking for it twice is how you
/// lose people on the first screen.
class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key});

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  final _formKey = GlobalKey<FormState>();
  final _identifier = TextEditingController();
  final _handle = TextEditingController();
  final _fullName = TextEditingController();
  final _password = TextEditingController();

  bool _registering = false;
  bool _obscure = true;
  bool _consent = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _error = context.read<Session>().startupError;
  }

  @override
  void dispose() {
    _identifier.dispose();
    _handle.dispose();
    _fullName.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    FocusScope.of(context).unfocus();
    if (!_formKey.currentState!.validate()) return;
    if (_registering && !_consent) {
      setState(() => _error = 'You need to accept the privacy notice to create an account.');
      return;
    }

    setState(() => _error = null);
    final session = context.read<Session>();
    try {
      if (_registering) {
        await session.signup(
          _handle.text.trim().toLowerCase(),
          _fullName.text.trim(),
          _password.text,
        );
      } else {
        await session.login(_identifier.text.trim(), _password.text);
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final busy = context.select<Session, bool>((s) => s.busy);

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(24, 32, 24, 32),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Center(child: Wordmark(size: 56)),
                    const SizedBox(height: 10),
                    Text(
                      'A campus network for verified Loyola students. '
                      'Nobody sees anything until their ID card checks out.',
                      textAlign: TextAlign.center,
                      style: theme.textTheme.bodyMedium?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 32),
                    SegmentedButton<bool>(
                      segments: const [
                        ButtonSegment(value: false, label: Text('Sign in')),
                        ButtonSegment(value: true, label: Text('Create account')),
                      ],
                      selected: {_registering},
                      onSelectionChanged: (value) => setState(() {
                        _registering = value.first;
                        _error = null;
                      }),
                    ),
                    const SizedBox(height: 24),
                    if (_registering) ..._signupFields() else ..._loginFields(),
                    if (_error != null) ...[
                      const SizedBox(height: 16),
                      _ErrorBanner(message: _error!),
                    ],
                    const SizedBox(height: 24),
                    FilledButton(
                      onPressed: busy ? null : _submit,
                      child: busy
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(strokeWidth: 2.2, color: Colors.white),
                            )
                          : Text(_registering ? 'Create account' : 'Sign in'),
                    ),
                    ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  List<Widget> _loginFields() => [
        TextFormField(
          controller: _identifier,
          autofillHints: const [AutofillHints.username],
          textInputAction: TextInputAction.next,
          decoration: const InputDecoration(
            labelText: 'Username or roll number',
            prefixIcon: Icon(Icons.person_outline_rounded),
          ),
          validator: (value) =>
              (value == null || value.trim().isEmpty) ? 'Enter your username or roll number.' : null,
        ),
        const SizedBox(height: 14),
        _passwordField(),
      ];

  List<Widget> _signupFields() => [
        TextFormField(
          controller: _fullName,
          textCapitalization: TextCapitalization.words,
          textInputAction: TextInputAction.next,
          decoration: const InputDecoration(
            labelText: 'Full name',
            helperText: 'Exactly as printed on your ID card.',
            prefixIcon: Icon(Icons.badge_outlined),
          ),
          validator: (value) =>
              (value == null || value.trim().length < 3) ? 'Enter your full name.' : null,
        ),
        const SizedBox(height: 14),
        TextFormField(
          controller: _handle,
          textInputAction: TextInputAction.next,
          inputFormatters: [
            FilteringTextInputFormatter.allow(RegExp(r'[a-zA-Z0-9_]')),
            LengthLimitingTextInputFormatter(24),
          ],
          decoration: const InputDecoration(
            labelText: 'Username',
            helperText: '3-24 characters: letters, numbers or underscores.',
            prefixIcon: Icon(Icons.alternate_email_rounded),
          ),
          validator: (value) {
            final handle = (value ?? '').trim();
            if (handle.length < 3) return 'Pick a username of at least 3 characters.';
            return null;
          },
        ),
        const SizedBox(height: 14),
        _passwordField(helper: 'At least 10 characters, with a letter and a number.'),
        const SizedBox(height: 8),
        CheckboxListTile(
          value: _consent,
          onChanged: (value) => setState(() => _consent = value ?? false),
          controlAffinity: ListTileControlAffinity.leading,
          contentPadding: EdgeInsets.zero,
          dense: true,
          title: Text(
            'I understand my ID card photo is encrypted, used only to verify me, '
            'and deleted automatically after 30 days.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ),
      ];

  Widget _passwordField({String? helper}) => TextFormField(
        controller: _password,
        obscureText: _obscure,
        autofillHints: const [AutofillHints.password],
        textInputAction: TextInputAction.done,
        onFieldSubmitted: (_) => _submit(),
        decoration: InputDecoration(
          labelText: 'Password',
          helperText: helper,
          helperMaxLines: 2,
          prefixIcon: const Icon(Icons.lock_outline_rounded),
          suffixIcon: IconButton(
            onPressed: () => setState(() => _obscure = !_obscure),
            icon: Icon(_obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined),
          ),
        ),
        validator: (value) {
          final password = value ?? '';
          if (password.isEmpty) return 'Enter your password.';
          if (_registering && password.length < 10) {
            return 'Use at least 10 characters.';
          }
          return null;
        },
      );
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: scheme.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.error_outline_rounded, size: 18, color: scheme.onErrorContainer),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: TextStyle(fontSize: 13.5, color: scheme.onErrorContainer, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }
}
