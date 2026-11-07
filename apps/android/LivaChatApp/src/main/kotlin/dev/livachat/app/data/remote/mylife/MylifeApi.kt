package dev.livachat.app.data.remote.mylife

import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.CancelAgentJobRequestDto
import dev.livachat.app.data.remote.mylife.dto.CancelAgentJobResponseDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDto
import dev.livachat.app.data.remote.mylife.dto.RemoveRequestDto
import dev.livachat.app.data.remote.mylife.dto.RemoveResponseDto
import dev.livachat.app.data.remote.mylife.dto.SearchResponseDto
import dev.livachat.app.data.remote.mylife.dto.TimelineResponseDto
import dev.livachat.sdk.LivaChatConfig
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.serializer
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.ResponseBody
import java.io.IOException
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Named
import kotlin.coroutines.resume
import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlin.coroutines.resumeWithException

/** BFF 错误信封 {error:{code,message}}，对齐 Web ApiError(status, code, message)。 */
class MylifeApiException(
    val status: Int,
    val code: String,
    message: String,
) : Exception(message)

class MylifeApi @Inject constructor(
    @Named("mylife") private val client: OkHttpClient,
    @Named("mylifeApiBase") private val baseUrl: HttpUrl,
    private val config: LivaChatConfig,
    @ApplicationContext private val context: Context,
) {
    private val json = Json { ignoreUnknownKeys = true }

    /**
     * remove 专用派生 client（docs/android_timeline_image_gallery.md §9.4.3）：删除按需求等待
     * 30s，基 client read=10s 不够；newBuilder 共享连接池/Dispatcher，仅覆写三个超时。
     */
    private val removeClient: OkHttpClient = client.newBuilder()
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .callTimeout(30, TimeUnit.SECONDS)
        .build()

    /**
     * agent job 取消专用派生 client：adapter abort RPC 自身 30s + 5s 宽限，
     * 外加重连/resume 余量，30s 基 client 不够（docs/android_agent_job_status.md §7.1）。
     */
    private val cancelClient: OkHttpClient = client.newBuilder()
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .callTimeout(60, TimeUnit.SECONDS)
        .build()

    /**
     * 日卡总览（docs/bff_service_interfaces.md §8）：`limit` = 天数页大小（缺省不发 = bff
     * 全量模式，老客户端语义）；`cursor` = 上一页最后一天（严格早于）或窗口定位日。
     */
    suspend fun overview(
        timezone: String? = null,
        deviceId: String? = null,
        limit: Int? = null,
        cursor: String? = null,
    ): OverviewDto = execute(
        "overview",
        mapOf(
            "timezone" to timezone,
            "device_id" to deviceId,
            "limit" to limit?.toString(),
            "cursor" to cursor,
        ),
        OverviewDto.serializer(),
    )

    /** V2 唯一新增端点（§5.3）：全局搜索，无缓存（查询词组合无限）。 */
    suspend fun search(q: String, limit: Int = 50, cursor: String? = null): SearchResponseDto =
        execute("search", mapOf("q" to q, "limit" to limit.toString(), "cursor" to cursor), SearchResponseDto.serializer())

    /** Gallery 帧列表（docs/android_timeline_image_gallery.md §3.6）：复用 /timeline（bff 零新增端点）。 */
    suspend fun timeline(
        dateFrom: String,
        dateTo: String,
        types: String = "video",
        limit: Int = 40,
        cursor: String? = null,
        timezone: String? = null,
    ): TimelineResponseDto = execute(
        "timeline",
        mapOf(
            "date_from" to dateFrom,
            "date_to" to dateTo,
            "types" to types,
            "limit" to limit.toString(),
            "cursor" to cursor,
            "timezone" to timezone,
        ),
        TimelineResponseDto.serializer(),
    )

    /**
     * 删除 video 事件（Gallery 编辑模式，docs/android_timeline_image_gallery.md §9.4.3）：
     * `POST events/remove`，body `{"event_refs": [...]}`（1–50，服务端上限）；**部分成功语义在
     * 响应体**（removed/skipped），HTTP 非 2xx 才抛（bad_request / removal_disabled /
     * upstream_unavailable）。超时 30s 走派生 client（基 client read=10s 不满足需求）。
     */
    suspend fun removeEvents(eventRefs: List<String>): RemoveResponseDto {
        val url = baseUrl.newBuilder().addPathSegments("events/remove")
            .addQueryParameter("token", config.token)
            .build()
        val body = json.encodeToString(RemoveRequestDto.serializer(), RemoveRequestDto(eventRefs))
            .toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder().url(url).post(body).build()
        return awaitParse(request, RemoveResponseDto.serializer(), callClient = removeClient)
    }

    /**
     * 取消一个 running agent job（docs/android_agent_job_status.md §7.1）：
     * `POST agent-jobs/cancel`，body `{"job_id": …}`；token/device_id query 对齐 removeEvents。
     */
    suspend fun cancelAgentJob(jobId: String): CancelAgentJobResponseDto {
        val url = baseUrl.newBuilder()
            .addPathSegments("agent-jobs/cancel")
            .addQueryParameter("token", config.token)
            .addQueryParameter("device_id", config.deviceId)
            .build()
        val body = json.encodeToString(
            CancelAgentJobRequestDto.serializer(), CancelAgentJobRequestDto(jobId),
        ).toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder().url(url).post(body).build()
        return awaitParse(
            request,
            CancelAgentJobResponseDto.serializer(),
            callClient = cancelClient,
        )
    }

    /**
     * 帧图字节下载（gallery 查看器保存到相册，§3.5）：`url` 为已绝对化附 token 的 media URL；
     * 非 2xx / 空体 / IO 失败抛异常（调用方收敛为"保存失败"）。
     */
    suspend fun downloadBytes(url: String): ByteArray {
        val request = Request.Builder().url(url).get().build()
        return suspendCancellableCoroutine { cont ->
            val call = client.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (!cont.isCancelled) cont.resumeWithException(e)
                }

                override fun onResponse(call: Call, response: Response) {
                    try {
                        val body = response.body
                        if (!response.isSuccessful) {
                            cont.resumeWithException(
                                MylifeApiException(response.code, "internal", "HTTP ${response.code}"),
                            )
                        } else if (body == null) {
                            cont.resumeWithException(MylifeApiException(response.code, "internal", context.getString(R.string.mylife_api_empty_response)))
                        } else {
                            cont.resume(body.bytes())
                        }
                    } catch (e: Exception) {
                        if (!cont.isCancelled) cont.resumeWithException(e)
                    } finally {
                        response.close()
                    }
                }
            })
        }
    }

    private suspend fun <T> execute(
        path: String,
        params: Map<String, String?>,
        deserializer: kotlinx.serialization.DeserializationStrategy<T>,
    ): T {
        val url = baseUrl.newBuilder().addPathSegments(path).apply {
            addQueryParameter("token", config.token)
            for ((k, v) in params) {
                if (!v.isNullOrEmpty()) addQueryParameter(k, v)
            }
        }.build()
        val request = Request.Builder().url(url).get().build()
        return awaitParse(request, deserializer)
    }

    private suspend fun <T> awaitParse(
        request: Request,
        deserializer: kotlinx.serialization.DeserializationStrategy<T>,
        callClient: OkHttpClient = client,
    ): T = suspendCancellableCoroutine { cont ->
        val call = callClient.newCall(request)
        cont.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (!cont.isCancelled) cont.resumeWithException(e)
            }

            override fun onResponse(call: Call, response: Response) {
                try {
                    val body = response.body
                    if (!response.isSuccessful) {
                        cont.resumeWithException(parseError(response.code, body))
                    } else if (body == null) {
                        cont.resumeWithException(MylifeApiException(response.code, "internal", context.getString(R.string.mylife_api_empty_response)))
                    } else {
                        val text = body.string()
                        try {
                            cont.resume(json.decodeFromString(deserializer, text))
                        } catch (e: Exception) {
                            cont.resumeWithException(
                                MylifeApiException(
                                    response.code,
                                    "internal",
                                    context.getString(R.string.mylife_api_json_parse_failed, e.message),
                                )
                            )
                        }
                    }
                } catch (e: Exception) {
                    if (!cont.isCancelled) cont.resumeWithException(e)
                } finally {
                    response.close()
                }
            }
        })
    }

    private fun parseError(status: Int, body: ResponseBody?): MylifeApiException {
        val text = try {
            body?.string()
        } catch (_: IOException) {
            null
        }
        if (text != null) {
            val envelope = runCatching {
                json.parseToJsonElement(text).jsonObject["error"]?.jsonObject
            }.getOrNull()
            if (envelope != null) {
                val code = envelope["code"]?.jsonPrimitive?.contentOrNull
                val message = envelope["message"]?.jsonPrimitive?.contentOrNull
                if (code != null) {
                    return MylifeApiException(status, code, message ?: "HTTP $status")
                }
            }
        }
        return MylifeApiException(status, "internal", "HTTP $status")
    }
}
