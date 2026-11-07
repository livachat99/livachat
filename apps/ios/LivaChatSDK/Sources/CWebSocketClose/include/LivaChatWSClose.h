#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// Foundation's Swift API only exposes the standard CloseCode cases, while
/// the underlying ObjC method accepts any NSInteger. LivaChat uses RFC 6455
/// application codes such as 4000 (client heartbeat/watchdog timeout).
FOUNDATION_EXPORT void LivaChatWebSocketCancelWithCode(
    NSURLSessionWebSocketTask *task,
    NSInteger code,
    NSData * _Nullable reason
);

NS_ASSUME_NONNULL_END
