package com.hellennjerik.prepaid_electricity_monitor

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "user_settings")
data class UserSetting(
    @PrimaryKey val userId: Int,
    val alertThreshold: Double = 5.0,
    val isSmsEnabled: Boolean = true
)