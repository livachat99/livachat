#import "LivaChatWSClose.h"

void LivaChatWebSocketCancelWithCode(
    NSURLSessionWebSocketTask *task,
    NSInteger code,
    NSData * _Nullable reason
) {
    [task cancelWithCloseCode:(NSURLSessionWebSocketCloseCode)code reason:reason];
}
