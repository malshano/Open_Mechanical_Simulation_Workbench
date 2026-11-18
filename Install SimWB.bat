@echo off
rem Mechanical Simulation Workbench - one-click install: double-click this file.
title Mechanical Simulation Workbench - Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Install-SimWB.ps1" %*
