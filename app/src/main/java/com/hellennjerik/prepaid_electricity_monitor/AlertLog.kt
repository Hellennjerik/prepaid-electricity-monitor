package com.hellennjerik.prepaid_electricity_monitor

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "alert_logs")
data class AlertLog(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val userId: Int,
    val message: String,
    val dateSent: Long = System.currentTimeMillis()
)