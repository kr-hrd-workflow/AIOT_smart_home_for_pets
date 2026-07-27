CREATE TABLE `__new_live_parts` (
	`home_id` text NOT NULL,
	`boot_id` text NOT NULL,
	`sequence` integer NOT NULL CHECK (`sequence` >= 0),
	`object_key` text NOT NULL UNIQUE,
	`sha256` text NOT NULL,
	`size_bytes` integer NOT NULL CHECK (`size_bytes` >= 0),
	`started_at` text NOT NULL,
	`duration_ms` integer NOT NULL CHECK (`duration_ms` IN (1000, 3000)),
	`created_at` text NOT NULL,
	`expires_at` text NOT NULL,
	PRIMARY KEY(`home_id`, `boot_id`, `sequence`),
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
INSERT INTO `__new_live_parts` (`home_id`, `boot_id`, `sequence`, `object_key`, `sha256`, `size_bytes`, `started_at`, `duration_ms`, `created_at`, `expires_at`)
SELECT `home_id`, `boot_id`, `sequence`, `object_key`, `sha256`, `size_bytes`, `started_at`, `duration_ms`, `created_at`, `expires_at` FROM `live_parts`;
--> statement-breakpoint
DROP TABLE `live_parts`;
--> statement-breakpoint
ALTER TABLE `__new_live_parts` RENAME TO `live_parts`;
--> statement-breakpoint
CREATE INDEX `live_parts_home_expires_idx` ON `live_parts` (`home_id`, `expires_at`);
