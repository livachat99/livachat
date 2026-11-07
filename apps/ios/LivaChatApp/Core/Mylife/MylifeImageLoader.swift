import Foundation
import NetworkImage

enum MylifeImageLoader {
    static func make(serverBase: ServerBase) -> DefaultNetworkImageLoader {
        let configuration = URLSessionConfiguration.default
        configuration.requestCachePolicy = .returnCacheDataElseLoad
        configuration.urlCache = URLCache(
            memoryCapacity: 10 * 1024 * 1024,
            diskCapacity: 100 * 1024 * 1024
        )
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 30
        configuration.httpAdditionalHeaders = ["Accept": "image/*"]

        let delegate = ProbeSessionDelegate(
            allowSelfSigned: serverBase.allowsSelfSigned,
            expectedHost: serverBase.host,
            expectedPort: serverBase.port
        )
        let session = URLSession(
            configuration: configuration,
            delegate: delegate,
            delegateQueue: nil
        )
        return DefaultNetworkImageLoader(
            cache: DefaultNetworkImageCache(countLimit: 100),
            session: session
        )
    }
}
