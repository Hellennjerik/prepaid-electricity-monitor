package com.hellennjerik.prepaid_electricity_monitor

data class RegisterRequest(
    val username: String,
    val password: String,
    val phone: String,
    val meter_number: String
)

data class LoginRequest(
    val username: String,
    val password: String
)

data class LoginResponse(
    val message: String,
    val user: UserInfo
)

data class UserInfo(
    val username: String,
    val meter_number: String
)

data class MeterStatus(
    val meter_number: String,
    val balance: Double,
    val kwh_used: Double,
    val status: String,
    val last_updated: String
)

data class BuyTokenRequest(
    val meter_number: String,
    val amount: Int
)

data class BuyTokenResponse(
    val message: String,
    val token: String,
    val amount: Int
)

data class ApplyTokenRequest(
    val token_string: String,
    val meter_number: String
)

data class SimpleResponse(
    val message: String? = null,
    val error: String? = null
)