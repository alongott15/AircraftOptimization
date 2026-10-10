import type { components } from './schema';

type S = components['schemas'];

export type User = S['UserOut'];
export type Reference = S['ReferenceOut'];
export type FleetSummary = S['FleetSummaryOut'];
export type Alert = S['AlertOut'];
export type TimelineItem = S['TimelineItemOut'];
export type SourceFreshness = S['SourceFreshnessOut'];
export type AircraftSummary = S['AircraftSummaryOut'];
export type AircraftDetail = S['AircraftDetailOut'];
export type AircraftPage = S['Page_AircraftSummaryOut_'];
export type Inspection = S['InspectionOut'];
export type Permit = S['PermitOut'];
export type MaintenanceAction = S['MaintenanceActionOut'];
export type Qualification = S['QualificationOut'];
export type Risk = S['RiskOut'];
export type Mission = S['MissionOut'];
export type MissionPage = S['Page_MissionOut_'];
export type MissionInput = S['MissionIn'];
export type OptimizationRun = S['OptimizationRunOut'];
export type Candidate = S['CandidateOut'];
export type CandidateSet = S['SetOut'];
export type Reason = S['ReasonOut'];
export type Assignment = S['AssignmentOut'];
export type Model = S['ModelOut'];
export type DurationSuggestion = S['DurationSuggestionOut'];
export type AuditEntry = S['AuditOut'];
export type AuditPage = S['Page_AuditOut_'];
export type HangarCapacity = S['HangarCapacityOut'];
export type HorizonPlan = S['HorizonPlanOut'];
export type HorizonMission = S['HorizonMissionOut'];
export type Weights = S['WeightsOut'];
export type TgWeight = S['TgWeightOut'];
export type TgWeightInput = S['TgWeightIn'];
export type ComplianceStatus = AircraftSummary['compliance_status'];
export type MaintenanceBoard = S['MaintenanceBoardOut'];
export type MaintenanceItem = S['MaintenanceItemOut'];
export type MaintenanceTaskInput = S['MaintenanceTaskIn'];
export type TaskStatus = MaintenanceItem['status'];
export type TaskAction = MaintenanceItem['action'];
export type MissionMaintenance = S['MissionMaintenanceOut'];
export type RecommendedAircraft = S['RecommendedAircraftOut'];

export interface Policy {
  duration_buffer_pct: number;
  min_duration_buffer_hours: number;
  calendar_buffer_hours: number;
  landing_buffer: number;
  turnaround_hours: number;
  idle_policy: 'EXCLUDE' | 'WARN';
  tolerance_policy: 'WARN' | 'NEVER';
  expiring_soon_days: number;
  expiring_soon_pct: number;
  expiring_soon_fh: number;
  expiring_soon_ldg: number;
  low_margin_pct: number;
  max_staleness_hours: number;
  risk_high_threshold: number;
  weights_preset: 'BALANCED' | 'MAX_SAFETY' | 'MAX_AVAILABILITY';
}

export interface FlightRow {
  takeoff_at: string;
  landing_at: string;
  mission_type: string;
  flight_hours: number;
  landings: number;
  touch_and_goes: number;
  engine_starts: number;
}
