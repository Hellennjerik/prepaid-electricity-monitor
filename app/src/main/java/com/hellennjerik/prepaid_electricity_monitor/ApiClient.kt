package com.hellennjerik.prepaid_electricity_monitor

import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {
    // Home Wi-Fi test:  "http://<your laptop's IPv4 address>:5000/"
    // Cloud:            "https://your-app-name.onrender.com/"   (HTTPS, no port)
    private const val BASE_URL = "http://192.168.100.23:5000/"

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

    val api: ApiService by lazy {
        Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }
}