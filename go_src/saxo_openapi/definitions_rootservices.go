package saxo_openapi // Changed package name

import "time"

// UserResponse mirrors the structure for /openapi/root/v1/user
type UserResponse struct {
	AuthenticationMethod      string    `json:"AuthenticationMethod"`
	ClientKey                 string    `json:"ClientKey"`
	EmployeeID                *string   `json:"EmployeeId"`
	Environment               string    `json:"Environment"`
	IsEmployee                bool      `json:"IsEmployee"`
	LastLoginStatus           string    `json:"LastLoginStatus"`
	LastLoginTime             time.Time `json:"LastLoginTime"`
	LegalAssetTypes           []string  `json:"LegalAssetTypes"`
	MarketDataViaOpenAPI      bool      `json:"MarketDataViaOpenApi"`
	Name                      string    `json:"Name"`
	OpenAPIVersion            string    `json:"OpenApiVersion"`
	PartnerPlatformID         *string   `json:"PartnerPlatformId"`
	PrimaryAccountKey         string    `json:"PrimaryAccountKey"`
	UserID                    string    `json:"UserId"`
	UserKey                   string    `json:"UserKey"`
	UserType                  string    `json:"UserType"`
	WarnProcessPending        bool      `json:"WarnProcessPending"`
	WarnTradableInstrumentsEx bool      `json:"WarnTradableInstrumentsEx"`
}

// SessionCapabilities represents the capabilities of a session.
type SessionCapabilities struct {
	ChangePassword             bool `json:"ChangePassword"`
	ChangePin                  bool `json:"ChangePin"`
	EditClientDetails          bool `json:"EditClientDetails"`
	EditTradingConditions      bool `json:"EditTradingConditions"`
	HasReferralSystem          bool `json:"HasReferralSystem"`
	Orders                     bool `json:"Orders"`
	Positions                  bool `json:"Positions"`
	Prices                     bool `json:"Prices"`
	ViewBalance                bool `json:"ViewBalance"`
	ViewClosedPositions        bool `json:"ViewClosedPositions"`
	ViewMargin                 bool `json:"ViewMargin"`
	ViewOrders                 bool `json:"ViewOrders"`
	ViewPositions              bool `json:"ViewPositions"`
	ViewTradableInstrumentList bool `json:"ViewTradableInstrumentList"`
}

// DiagnosticsResponse mirrors the structure for /openapi/root/v1/diagnostics
type DiagnosticsResponse struct {
	AppGitCommit          string    `json:"AppGitCommit"`
	AppName               string    `json:"AppName"`
	AssemblyFullName      string    `json:"AssemblyFullName"`
	AssemblyVersion       string    `json:"AssemblyVersion"`
	CoreCLRVersion        string    `json:"CoreCLRVersion"`
	CurrentCulture        string    `json:"CurrentCulture"`
	CurrentUICulture      string    `json:"CurrentUICulture"`
	GitCommit             string    `json:"GitCommit"`
	MachineName           string    `json:"MachineName"`
	OSVersion             string    `json:"OSVersion"`
	ProcessStartTime      time.Time `json:"ProcessStartTime"`
	ServiceableGitCommit  string    `json:"ServiceableGitCommit"`
	CurrentServerTimeUTC  time.Time `json:"CurrentServerTimeUtc"`
	ServiceMemoryUsageMB  int       `json:"ServiceMemoryUsageMb"`
	SwaggerVersion        string    `json:"SwaggerVersion"`
	TotalMemoryUsageMB    int       `json:"TotalMemoryUsageMb"`
	TradingCoreVersion    string    `json:"TradingCoreVersion"`
	ServiceReady          bool      `json:"ServiceReady"`
}

type Feature struct {
    Name    string `json:"Name"`
    Enabled bool   `json:"Enabled"`
}

type FeaturesResponse struct {
    Data []Feature `json:"Data"`
}

type ClientResponse struct {
    AccountValueProtectionLimit float64   `json:"AccountValueProtectionLimit"`
    ClientID                    string    `json:"ClientId"`
    ClientKey                   string    `json:"ClientKey"`
    Currency                    string    `json:"Currency"`
    CurrencySymbol              string    `json:"CurrencySymbol"`
    DailyAccountFundingLimit    float64   `json:"DailyAccountFundingLimit"`
    DefaultAccountID            string    `json:"DefaultAccountId"`
    DefaultAccountKey           string    `json:"DefaultAccountKey"`
    IsMarginTradingAllowed      bool      `json:"IsMarginTradingAllowed"`
    IsVariationMarginEligible   bool      `json:"IsVariationMarginEligible"`
    LegalAssetTypes             []string  `json:"LegalAssetTypes"`
    Name                        string    `json:"Name"`
    PartnerPlatformID           *string   `json:"PartnerPlatformId"`
    PositionNettingMethod       string    `json:"PositionNettingMethod"`
    SupportsAccountValueShield  bool      `json:"SupportsAccountValueShield"`
}

type ApplicationResponse struct {
    AppKey      string   `json:"AppKey"`
    AppName     string   `json:"AppName"`
    Description string   `json:"Description"`
    Owner       string   `json:"Owner"`
    Permissions []string `json:"Permissions"`
}
