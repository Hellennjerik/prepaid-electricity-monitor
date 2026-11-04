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

    @GET("solar_status/{meter_number}")
    suspend fun getSolarStatus(@Path("meter_number") meterNumber: String): Response<SolarStatus>

    @GET("predict/{meter_number}")
    suspend fun getPrediction(@Path("meter_number") meterNumber: String): Response<PredictionResponse>

    @POST("register_appliances")
    suspend fun registerAppliances(@Body request: RegisterAppliancesRequest): Response<SimpleResponse>

    @GET("analyze_appliances/{meter_number}")
    suspend fun analyzeAppliances(@Path("meter_number") meterNumber: String): Response<ApplianceAnalysisResponse>

    @GET("history/{meter_number}")
    suspend fun getHistory(
        @Path("meter_number") meterNumber: String,
        @Query("days") days: Int = 7
    ): Response<HistoryResponse>
}