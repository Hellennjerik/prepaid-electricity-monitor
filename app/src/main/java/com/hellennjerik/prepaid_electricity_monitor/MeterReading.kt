package com.hellennjerik.prepaid_electricity_monitor

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "meter_readings")
data class MeterReading(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val userId: Int,
    val unitsRemaining: Double,
    val source: String = "MANUAL",
    val timestamp: Long = System.currentTimeMillis()
)