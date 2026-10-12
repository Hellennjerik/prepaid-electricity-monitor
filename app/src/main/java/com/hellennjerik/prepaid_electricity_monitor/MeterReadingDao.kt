package com.hellennjerik.prepaid_electricity_monitor

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface MeterReadingDao {
    @Insert
    suspend fun insertReading(reading: MeterReading)

    @Query("SELECT * FROM meter_readings ORDER BY timestamp DESC LIMIT 1")
    fun getLatestReading(): Flow<MeterReading?>
}