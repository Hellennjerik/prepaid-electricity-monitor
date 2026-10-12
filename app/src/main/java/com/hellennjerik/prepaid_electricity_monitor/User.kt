package com.hellennjerik.prepaid_electricity_monitor

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "users")
data class User(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val fullName: String,
    val email: String,
    val phoneNumber: String,
    val password: String,
    val role: String = "PREPAID_CUSTOMER" // or "SYSTEM_MANAGER"
)