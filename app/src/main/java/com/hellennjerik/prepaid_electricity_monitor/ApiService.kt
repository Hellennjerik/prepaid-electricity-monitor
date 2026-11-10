package com.hellennjerik.prepaid_electricity_monitor

import retrofit2.Response
import retrofit2.http.*

interface ApiService {
    // ---- accounts ----
    @POST("auth/register")
    suspend fun signUp(@Body request: SignUpRequest): Response<SignUpReply>

    @POST("auth/verify")
    suspend fun verify(@Body request: VerifyRequest): Response<AuthResponse>

    @POST("auth/resend")
    suspend fun resendCode(@Body request: ResendRequest): Response<SimpleResponse>

    @POST("auth/login")
    suspend fun signIn(@Body request: SignInRequest): Response<AuthResponse>

    @GET("me")
    suspend fun getMe(): Response<AccountProfile>

    @PUT("me")
    suspend fun updateMe(@Body request: UpdateProfileRequest): Response<AccountProfile>

    @POST("me/meters")
    suspend fun addMeter(@Body request: AddMeterRequest): Response<AccountProfile>

    // ---- meter ----
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

    // ---- appliances ----
    @GET("appliance_catalog")
    suspend fun getCatalog(): Response<CatalogResponse>

    @GET("appliances/{meter_number}")
    suspend fun getSavedAppliances(@Path("meter_number") meterNumber: String): Response<SavedAppliancesResponse>

    @POST("register_appliances")
    suspend fun registerAppliances(@Body request: RegisterAppliancesRequest): Response<SimpleResponse>

    @GET("analyze_appliances/{meter_number}")
    suspend fun analyzeAppliances(@Path("meter_number") meterNumber: String): Response<ApplianceAnalysisResponse>

    // ---- reports and insights ----
    @GET("history/{meter_number}")
    suspend fun getHistory(
        @Path("meter_number") meterNumber: String,
        @Query("days") days: Int = 7
    ): Response<HistoryResponse>

    @GET("reports/{meter_number}")
    suspend fun getReport(
        @Path("meter_number") meterNumber: String,
        @Query("period") period: String
    ): Response<ReportResponse>

    @GET("planner/{meter_number}")
    suspend fun getPlanner(
        @Path("meter_number") meterNumber: String,
        @Query("amount") amount: Double
    ): Response<PlannerResponse>

    @GET("weather/{meter_number}")
    suspend fun getWeather(@Path("meter_number") meterNumber: String): Response<WeatherResponse>

    @GET("alerts/{meter_number}")
    suspend fun getAlerts(@Path("meter_number") meterNumber: String): Response<AlertsResponse>

    @POST("feedback")
    suspend fun sendFeedback(@Body request: FeedbackRequest): Response<SimpleResponse>
}
