import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'config.dart';

/// A failed API call, already translated into something worth showing a person.
class ApiException implements Exception {
  ApiException(this.statusCode, this.message, {this.code});

  final int statusCode;
  final String message;

  /// Machine-readable marker the server sets on the cases the app must react to
  /// rather than merely report — `verification_required`, `unauthenticated`.
  final String? code;

  bool get needsVerification => code == 'verification_required';
  bool get needsLogin => statusCode == 401;

  @override
  String toString() => message;
}

/// Thin HTTP layer over the JSON API.
///
/// Everything above this class deals in decoded maps and [ApiException]; no
/// screen should ever see a status code or a raw body.
class ApiClient {
  ApiClient({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  /// The opaque session token, or null when signed out.
  String? token;

  /// Called when the server rejects our token, so the app can drop to sign-in
  /// from wherever it happens to be.
  void Function()? onUnauthenticated;

  Map<String, String> _headers({bool json = true}) => {
        if (json) 'Content-Type': 'application/json',
        'Accept': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
      };

  /// Headers for loading a protected image, which needs auth but not JSON.
  Map<String, String> get imageHeaders =>
      {if (token != null) 'Authorization': 'Bearer $token'};

  Uri _uri(String path, [Map<String, dynamic>? query]) {
    final base = Uri.parse('${AppConfig.apiRoot}$path');
    if (query == null || query.isEmpty) return base;
    final params = <String, String>{};
    query.forEach((key, value) {
      if (value != null) params[key] = '$value';
    });
    return base.replace(queryParameters: {...base.queryParameters, ...params});
  }

  Future<dynamic> get(String path, {Map<String, dynamic>? query}) =>
      _send(() => _client.get(_uri(path, query), headers: _headers(json: false)));

  Future<dynamic> post(String path, {Object? body, Map<String, dynamic>? query}) => _send(
        () => _client.post(
          _uri(path, query),
          headers: _headers(),
          body: body == null ? null : jsonEncode(body),
        ),
      );

  Future<dynamic> patch(String path, {Object? body}) => _send(
        () => _client.patch(
          _uri(path),
          headers: _headers(),
          body: body == null ? null : jsonEncode(body),
        ),
      );

  Future<dynamic> delete(String path) =>
      _send(() => _client.delete(_uri(path), headers: _headers(json: false)));

  /// Multipart upload — ID-card captures, selfies and post images.
  Future<dynamic> upload(
    String path, {
    required String field,
    required String filePath,
    Map<String, String> fields = const {},
  }) =>
      _send(() async {
        final request = http.MultipartRequest('POST', _uri(path))
          ..headers.addAll(_headers(json: false))
          ..fields.addAll(fields)
          ..files.add(await http.MultipartFile.fromPath(field, filePath));
        final streamed = await request.send();
        return http.Response.fromStream(streamed);
      });

  Future<dynamic> _send(Future<http.Response> Function() run) async {
    final http.Response response;
    try {
      response = await run().timeout(const Duration(seconds: 30));
    } on SocketException {
      throw ApiException(
        0,
        'Cannot reach ${AppConfig.baseUrl}. Check your connection, or the '
        'server address in Settings.',
      );
    } on HttpException {
      throw ApiException(0, 'The connection dropped mid-request. Try again.');
    } catch (error) {
      throw ApiException(0, 'Network error: $error');
    }

    if (response.statusCode == 204 || response.body.isEmpty) {
      if (response.statusCode >= 400) {
        _raise(response.statusCode, null);
      }
      return null;
    }

    dynamic decoded;
    try {
      decoded = jsonDecode(utf8.decode(response.bodyBytes));
    } on FormatException {
      if (response.statusCode >= 400) {
        throw ApiException(response.statusCode, 'The server returned an unexpected response.');
      }
      throw ApiException(response.statusCode, 'Could not read the server response.');
    }

    if (response.statusCode >= 400) {
      _raise(response.statusCode, decoded);
    }
    return decoded;
  }

  Never _raise(int status, dynamic decoded) {
    var message = 'Something went wrong (HTTP $status).';
    String? code;
    if (decoded is Map) {
      code = decoded['code'] as String?;
      final detail = decoded['detail'];
      if (detail is String) {
        message = detail;
      } else if (detail is List && detail.isNotEmpty) {
        // FastAPI validation errors arrive as a list of field problems.
        final first = detail.first;
        if (first is Map && first['msg'] is String) {
          final field = (first['loc'] as List?)?.last;
          message = field == null ? '${first['msg']}' : '${first['msg']} (${field.toString()})';
        }
      }
    }
    if (status == 401) {
      onUnauthenticated?.call();
    }
    throw ApiException(status, message, code: code);
  }

  void close() => _client.close();
}
