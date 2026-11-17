package com.hellennjerik.prepaid_electricity_monitor

import android.content.Context
import android.content.SharedPreferences
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Talks to the NextHome server. The server address is stored on the phone and can be changed
 * from the login screen ("Server: ... tap to change"), so you never need to rebuild the app
 * when the laptop's IP changes or when you switch to the hosted server.
 *
 *   Hosted server:         https://yourname.pythonanywhere.com/
 *   Laptop over USB cable: http://127.0.0.1:5000/   (after: adb reverse tcp:5000 tcp:5000)
 *   Emulator:              http://10.0.2.2:5000/
 */
object ApiClient {
    const val DEFAULT_URL = "http://127.0.0.1:5000/"

    private lateinit var prefs: SharedPreferences
    private var built: Pair<String, ApiService>? = null

    fun init(context: Context) {
        if (!::prefs.isInitialized) {
            prefs = context.applicationContext.getSharedPreferences("Server", Context.MODE_PRIVATE)
        }
    }

    var baseUrl: String
        get() = if (::prefs.isInitialized) prefs.getString("url", DEFAULT_URL) ?: DEFAULT_URL else DEFAULT_URL
        set(value) { if (::prefs.isInitialized) prefs.edit().putString("url", value).apply() }

    /** Turns what the user typed into a usable address, or null if it is not one. */
    fun normalise(raw: String): String? {
        var text = raw.trim()
        if (text.isEmpty()) return null
        if (!text.startsWith("http://") && !text.startsWith("https://")) {
            val local = text.startsWith("localhost") || text.startsWith("127.") || text.startsWith("10.") ||
                text.startsWith("192.168.") || text.matches(Regex("^[0-9.]+(:[0-9]+)?.*"))
            text = (if (local) "http://" else "https://") + text
        }
        if (!text.endsWith("/")) text += "/"
        return if (text.toHttpUrlOrNull() != null) text else null
    }

    private val logging = HttpLoggingInterceptor().apply {
        level = HttpLoggingInterceptor.Level.BODY
    }

    // Adds "Authorization: Bearer <token>" to every call once the user is logged in.
    private val auth = okhttp3.Interceptor { chain ->
        val token = SessionStore.token
        val request = if (token.isNullOrEmpty()) chain.request()
        else chain.request().newBuilder().header("Authorization", "Bearer $token").build()
        chain.proceed(request)
    }

    private val client = OkHttpClient.Builder()
        .addInterceptor(auth)
        .addInterceptor(logging)
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    val api: ApiService
        get() {
            val url = baseUrl
            built?.let { if (it.first == url) return it.second }
            val service = Retrofit.Builder()
                .baseUrl(url)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
                .create(ApiService::class.java)
            built = url to service
            return service
        }
}
