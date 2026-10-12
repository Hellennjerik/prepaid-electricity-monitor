package com.hellennjerik.prepaid_electricity_monitor

import retrofit2.Response
import retrofit2.http.*

interface ApiService {
    @POST("register")
    suspend fun register(@Body request: RegisterRequest): Response<SimpleResponse>

    @POST("login")
    suspend fun login(@Body request: LoginRequest): Response<LoginResponse>

    @GET("meter_status/{meter_number}")
    suspend fun getMeterStatus(@Path("meter_number") meterNumber: String): Response<MeterStatus>

    @POST("buy_token")
    suspend fun buyToken(@Body request: BuyTokenRequest): Response<BuyTokenResponse>

    @POST("apply_token")
    suspend fun applyToken(@Body request: ApplyTokenRequest): Response<SimpleResponse>
}