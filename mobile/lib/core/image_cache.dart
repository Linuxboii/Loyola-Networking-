import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_cache_manager/flutter_cache_manager.dart';

/// Bounded cache for authenticated campus media.
///
/// The server rotates signed URLs twice a day. Keeping files for seven days
/// makes scrolling and app restarts instant while the object cap prevents the
/// cache from growing without limit. [clear] is called whenever a session ends
/// so one member's private media is not retained for the next member.
class CampusImageCache {
  CampusImageCache._();

  static final CacheManager manager = CacheManager(
    Config(
      'loyolaCampusMediaV1',
      stalePeriod: const Duration(days: 7),
      maxNrOfCacheObjects: 250,
    ),
  );

  static Future<void> clear() async {
    await manager.emptyCache();
    PaintingBinding.instance.imageCache
      ..clear()
      ..clearLiveImages();
  }
}

class CampusCachedImage extends StatelessWidget {
  const CampusCachedImage({
    super.key,
    required this.url,
    required this.httpHeaders,
    this.fit = BoxFit.cover,
    this.width,
    this.height,
  });

  final String url;
  final Map<String, String> httpHeaders;
  final BoxFit fit;
  final double? width;
  final double? height;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, constraints) {
          final logicalWidth = width ?? constraints.maxWidth;
          final finiteWidth = logicalWidth.isFinite ? logicalWidth : 1080.0;
          final decodeWidth =
              (finiteWidth * MediaQuery.devicePixelRatioOf(context)).round();
          return CachedNetworkImage(
            cacheManager: CampusImageCache.manager,
            imageUrl: url,
            httpHeaders: httpHeaders,
            fit: fit,
            width: width,
            height: height,
            memCacheWidth: decodeWidth.clamp(1, 2160).toInt(),
            fadeInDuration: const Duration(milliseconds: 90),
            fadeOutDuration: Duration.zero,
            useOldImageOnUrlChange: true,
            placeholder: (context, _) => Container(
              height: height ?? 180,
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
            ),
            errorWidget: (context, _, __) => const SizedBox.shrink(),
          );
        },
      );
}

class CampusCachedImageProvider extends CachedNetworkImageProvider {
  CampusCachedImageProvider(
    super.url, {
    required Map<String, String> httpHeaders,
  }) : super(
          headers: httpHeaders,
          cacheManager: CampusImageCache.manager,
        );
}
